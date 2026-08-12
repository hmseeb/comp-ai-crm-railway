#!/usr/bin/env python3
"""Publishes the template, then sets the icon separately.

The slug is minted from the name at first publish and never moves afterwards, so
this runs once. The icon goes through templateUpsertSettings rather than being
passed to publish, because that one does not re-mint the slug.

Usage: publish.py <templateId>
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path("~/.claude/skills/create-template-for-railway/scripts").expanduser()))
import rw  # noqa: E402

WORKSPACE = "fc4796db-2c6c-4354-a564-d4a1d900af53"  # Auromations
NAME = "Comp AI CRM"
CATEGORY = "Automation"
DESCRIPTION = "AI sales CRM whose research agent fills in contacts, deals and pipeline"
ICON = (
    "https://raw.githubusercontent.com/trycompai/crm/"
    "f2484fb08d1dd1357c1e3deddb97610cd8e6f1ed/apps/app/public/web-app-manifest-512x512.png"
)

template_id = sys.argv[1]
readme = pathlib.Path(__file__).with_name("TEMPLATE_OVERVIEW.md").read_text()

assert len(DESCRIPTION) <= 75, f"description is {len(DESCRIPTION)} characters"

published = rw.gql(
    """mutation($id: String!, $input: TemplatePublishInput!) {
         templatePublish(id: $id, input: $input) { id code name isApproved }
       }""",
    {
        "id": template_id,
        "input": {
            "category": CATEGORY,
            "description": DESCRIPTION,
            "readme": readme,
            "workspaceId": WORKSPACE,
        },
    },
)["templatePublish"]
print("published", published)

rw.gql(
    """mutation($id: String!, $input: TemplateUpsertSettingsInput!) {
         templateUpsertSettings(id: $id, input: $input) { id code image }
       }""",
    {
        "id": template_id,
        "input": {
            "workspaceId": WORKSPACE,
            "name": NAME,
            "image": ICON,
            "description": DESCRIPTION,
        },
    },
    internal=True,
)
print(f"icon set\nhttps://railway.com/deploy/{published['code']}")
