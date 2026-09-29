# Offline mock-plan placeholders only. No account, certificate, image or secret is selected.
account_id                      = "000000000000"
region                          = "us-west-2"
availability_zones              = ["us-west-2a", "us-west-2b"]
client_cidrs                    = ["192.0.2.1/32"]
domain                          = "planner.trial.example.com"
certificate_arn                 = "arn:aws:acm:us-west-2:000000000000:certificate/00000000-0000-0000-0000-000000000000"
image                           = "000000000000.dkr.ecr.us-west-2.amazonaws.com/convoy-planner@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
planner_verification_secret_arn = "arn:aws:secretsmanager:us-west-2:000000000000:secret:convoy-trial/public-planner-aaaaaa"
planner_probe_secret_arn        = "arn:aws:secretsmanager:us-west-2:000000000000:secret:convoy-trial/planner-probe-bbbbbb"
planner_enabled                 = false
release_json                    = <<-JSON
{
  "schema_version": 2,
  "profile": "metaworld-smolvla-text-skill-v1",
  "action_manifest": {
    "schema_version": 1,
    "profile": "metaworld-smolvla-pick-place-rgb-v1",
    "policy": {
      "runtime": "qualified-action",
      "artifact_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    },
    "environment": {
      "name": "pick-place-v3",
      "metaworld": "3.0.0",
      "mujoco": "3.3.0"
    },
    "execution": {
      "max_steps": 500,
      "decision_timeout_ms": 5000,
      "mission_timeout_s": 300
    }
  },
  "planner": {
    "runtime": "convoy-llamacpp-text-skill-v1",
    "artifact_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "protocol_sha256": "5bfb3115ff1cee4e9bea4399dadb33bd4481c95fd12433372e43f71817fd8afc"
  },
  "task": {
    "instruction": "Pick and place a puck to a goal",
    "skill_id": "pick_place_puck"
  },
  "catalog_sha256": "c1ea5b50c18a842b7a5486d96e3c68423cd35047c8d652b498ef863bf544072b",
  "planning": {
    "timeout_ms": 30000
  },
  "placement": {
    "policy": "development-local-cpu",
    "planner": "development-remote-cpu"
  }
}
JSON
release_sha256                  = "332d77974c76edce2d57efab45ce772fbe34f8a32c0eeb76e55409fa17343978"
