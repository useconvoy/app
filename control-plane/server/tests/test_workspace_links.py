"""Saved specs link to owned applications without changing releases or execution."""
from copy import deepcopy

from conftest import WEB, login, make_user
from fastapi.testclient import TestClient
from test_configuration_releases import configuration
from test_platform_lifecycle import post
from test_robot_registry import profile
from test_workspace_documents import delete, put

ROOT = "/api/v1/workspace-configuration-links"
DOC = {"schemaVersion": 1, "configurations": [{"id": "arm-config", "name": "Saved arm", "edgeModels": ["declared-only"]}]}


def setup(admin):
    project = post(admin, "/api/v1/projects", {"name": "Lab"})
    prof = profile(admin, project)
    result = post(admin, "/api/v1/configurations", {"project_id": project["id"], "name": "Actual arm", "configuration": configuration(prof)})
    assert put(admin, "configurations", DOC, "document").status_code == 200
    body = {"configuration_id": "arm-config", "application_id": result["application"]["id"], "document_revision": 1, "expected_link_id": None}
    return project, result, body


def test_link_lifecycle_preserves_source_and_releases_and_detects_conflicts(app, admin):
    project, result, body = setup(admin)
    linked = post(admin, ROOT, body)
    assert post(admin, ROOT, body) == linked
    assert linked["source_state"] == "unchanged"
    source_url = ROOT + "?configuration_id=arm-config"
    application_url = ROOT + "?application_id=" + result["application"]["id"]
    assert admin.get(source_url).json() == admin.get(application_url).json() == [linked]
    assert admin.get("/api/v1/workspace-documents/configurations").json()["body"] == DOC
    assert admin.get(f"/api/v1/applications/{body['application_id']}/releases").json() == [result["release"]]
    assert admin.get(f"/api/v1/deployments?project_id={project['id']}").json() == []
    post(admin, ROOT, body, key="conflicting-tab", expected=409)
    # A document update unrelated to this configuration does not mark the link changed.
    updated = {**deepcopy(DOC), "activity": [{"message": "New result"}]}
    assert put(admin, "configurations", updated, "unrelated", revision=1).status_code == 200
    assert admin.get(source_url).json()[0]["source_state"] == "unchanged"
    updated["configurations"][0]["name"] = "Changed arm"
    assert put(admin, "configurations", updated, "changed", revision=2).status_code == 200
    assert admin.get(source_url).json()[0]["source_state"] == "changed"
    post(admin, ROOT, {**body, "expected_link_id": linked["id"]}, key="stale-document", expected=409)
    reviewed = post(admin, ROOT, {**body, "document_revision": 3, "expected_link_id": linked["id"]}, key="reviewed")
    assert reviewed["source_state"] == "unchanged" and reviewed["id"] != linked["id"]
    post(admin, ROOT + f"/{linked['id']}/remove", {}, expected=404)
    updated["configurations"] = []
    assert put(admin, "configurations", updated, "removed-config", revision=3).status_code == 200
    assert admin.get(application_url).json()[0]["source_state"] == "missing"
    post(admin, ROOT, {**body, "document_revision": 4, "expected_link_id": reviewed["id"]}, key="missing", expected=404)
    assert post(admin, ROOT + f"/{reviewed['id']}/remove", {}, expected=200)["deleted"]
    assert post(admin, ROOT + f"/{reviewed['id']}/remove", {}, expected=200)["deleted"]
    assert admin.get(application_url).json() == []
    assert put(admin, "configurations", DOC, "restore", revision=4).status_code == 200
    post(admin, ROOT, {**body, "document_revision": 5}, key="restore-link")
    # Document deletion cascades links; the executable application is preserved.
    assert delete(admin, "configurations", "delete-document", revision=5).status_code == 204
    assert admin.get(application_url).json() == []
    assert put(admin, "configurations", DOC, "recreate").status_code == 200
    assert admin.get(source_url).json() == []
    assert admin.get(f"/api/v1/applications/{body['application_id']}/releases").json() == [result["release"]]


def test_links_require_owned_document_application_and_operator(app, admin):
    _, result, body = setup(admin)
    link = post(admin, ROOT, body)
    make_user(admin, "other@example.test", "admin")
    with TestClient(app) as other:
        login(other, "other@example.test", "password-123")
        assert put(other, "configurations", DOC, "mine").status_code == 200
        assert other.get(ROOT + "?configuration_id=arm-config").json() == []
        assert other.get(ROOT + "?application_id=" + body["application_id"]).status_code == 404
        post(other, ROOT, body, expected=404)
        post(other, ROOT + f"/{link['id']}/remove", {}, expected=404)
        project = post(other, "/api/v1/projects", {"name": "Other"})
        app_other = post(other, "/api/v1/applications", {"project_id": project["id"], "name": "Other app"})
    post(admin, ROOT, {**body, "application_id": app_other["id"], "expected_link_id": link["id"]}, key="foreign-app", expected=404)
    make_user(admin, "viewer@example.test", "viewer")
    with TestClient(app) as viewer:
        login(viewer, "viewer@example.test", "password-123")
        post(viewer, ROOT, body, expected=403)
        post(viewer, ROOT + f"/{link['id']}/remove", {}, expected=403)
    assert admin.post(ROOT, json=body, headers={"Idempotency-Key": "no-csrf"}).status_code == 403
    assert admin.post(ROOT, json=body, headers=WEB).status_code == 422
    assert admin.get(ROOT).status_code == 422
    assert admin.get(ROOT + "?configuration_id=arm-config&application_id=" + body["application_id"]).status_code == 422
    assert admin.get(f"/api/v1/applications/{body['application_id']}").json() == result["application"]


def test_link_rejects_ambiguous_or_unsupported_source_without_replacing_link(app, admin):
    _, _, body = setup(admin)
    linked = post(admin, ROOT, body)
    assert put(admin, "configurations", {**DOC, "configurations": DOC["configurations"] * 2}, "ambiguous", revision=1).status_code == 200
    assert admin.get(ROOT + "?configuration_id=arm-config").json()[0]["source_state"] == "unsupported"
    post(admin, ROOT, {**body, "document_revision": 2, "expected_link_id": linked["id"]}, key="bad-link", expected=422)
    assert put(admin, "configurations", {**DOC, "schemaVersion": 2}, "unsupported", revision=2).status_code == 200
    post(admin, ROOT, {**body, "document_revision": 3, "expected_link_id": linked["id"]}, key="unsupported", expected=422)
    assert admin.get(ROOT + "?configuration_id=arm-config").json()[0]["id"] == linked["id"]
