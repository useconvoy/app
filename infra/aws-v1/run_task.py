"""Run one explicit maintenance task after a reviewed apply; never used in validation.

Reads non-secret Terraform outputs and uses the operator's existing AWS CLI role.
Requires every service to be stopped before bootstrap/migration. Does not seed
secrets, change desired service counts, run Terraform apply or delete anything.
"""
import argparse
import json
import subprocess
import time
from pathlib import Path


def aws(region, *args):
    result = subprocess.run(["aws", "--region", region, *args, "--output", "json"],
                            check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["bootstrap", "migrate"])
    parser.add_argument("--region", required=True)
    args = parser.parse_args()
    result = subprocess.run(["terraform", "output", "-json"], cwd=Path(__file__).parent,
                            check=True, capture_output=True, text=True)
    outputs = {key: value["value"] for key, value in json.loads(result.stdout).items()}
    identity = outputs["deployment_identity"]
    if args.region != identity["region"] or aws(args.region, "sts", "get-caller-identity")["Account"] != identity["account_id"]:
        raise RuntimeError("AWS identity/region differs from the reviewed Terraform installation")
    cluster = outputs["cluster"]
    services = aws(args.region, "ecs", "list-services", "--cluster", cluster)["serviceArns"]
    if services:
        state = aws(args.region, "ecs", "describe-services", "--cluster", cluster, "--services", *services)
        if state.get("failures") or any(service[field] for service in state["services"]
                                         for field in ("desiredCount", "runningCount", "pendingCount")):
            raise RuntimeError("stop every application service before running database maintenance")
    if aws(args.region, "ecs", "list-tasks", "--cluster", cluster)["taskArns"]:
        raise RuntimeError("an existing standalone task is still running; inspect it before retrying")
    task = outputs["one_off_tasks"][args.action]
    launched = aws(args.region, "ecs", "run-task", "--cluster", cluster, "--launch-type", "FARGATE",
                   "--platform-version", "1.4.0", "--task-definition", task["task_definition"],
                   "--network-configuration", json.dumps(task["network_configuration"]))
    if launched.get("failures") or len(launched.get("tasks", [])) != 1:
        raise RuntimeError("ECS did not admit exactly one maintenance task; inspect service events")
    arn = launched["tasks"][0]["taskArn"]
    print("Started", arn, flush=True)
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        state = aws(args.region, "ecs", "describe-tasks", "--cluster", cluster, "--tasks", arn)
        if state.get("failures") or len(state.get("tasks", [])) != 1:
            raise RuntimeError("task inspection failed; inspect the original task before retrying")
        task_state = state["tasks"][0]
        if task_state["lastStatus"] == "STOPPED":
            containers = task_state.get("containers", [])
            if len(containers) != 1 or containers[0].get("exitCode") != 0:
                raise RuntimeError("maintenance failed; inspect the named task's CloudWatch log")
            print("Succeeded:", args.action)
            return
        time.sleep(5)
    raise RuntimeError("maintenance wait timed out; original task was left intact for inspection")


if __name__ == "__main__":
    main()
