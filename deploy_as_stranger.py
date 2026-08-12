#!/usr/bin/env python3
"""Deploys the template into a brand new project, which is what the button does.

Omitting projectId is the whole difference between this and a redeploy: the
references only resolve against services that are being created together, so a
config that works when reapplied to the source project can still be broken for
everyone who actually clicks deploy.

Usage: deploy_as_stranger.py <templateCode> <answers.json>
"""

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path("~/.claude/skills/create-template-for-railway/scripts").expanduser()))
import rw  # noqa: E402

WORKSPACE = "fc4796db-2c6c-4354-a564-d4a1d900af53"  # Auromations


def main():
    code = sys.argv[1]
    answers = json.loads(pathlib.Path(sys.argv[2]).read_text()) if len(sys.argv) > 2 else {}

    template = rw.gql(
        "query($c: String!) { template(code: $c) { id serializedConfig } }",
        {"c": code},
    )["template"]
    config = template["serializedConfig"]

    # What the deploy form collects, written straight into the config the way
    # the dashboard does it.
    for service in config["services"].values():
        for key, value in answers.get(service["name"], {}).items():
            service.setdefault("variables", {}).setdefault(key, {})["defaultValue"] = value

    result = rw.gql(
        """mutation($input: TemplateDeployV2Input!) {
             templateDeployV2(input: $input) { projectId workflowId }
           }""",
        {
            "input": {
                "templateId": template["id"],
                "workspaceId": WORKSPACE,
                "serializedConfig": config,
            }
        },
    )["templateDeployV2"]

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
