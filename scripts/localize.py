#!/usr/bin/env python3
"""Turn the sanitized exports in workflows/ into importable copies with YOUR ids.

    python3 scripts/localize.py          # reads workflows/raw/local-ids.json, writes workflows/raw/*.local.json

workflows/raw/ is gitignored. Fill local-ids.json once (base id, table ids, Slack channel ids, timezone, n8n credential ids/names).
Credential ids are visible in the n8n URL when you open a credential (…/credentials/<id>).
"""
import json, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "workflows" / "raw"
IDS = RAW / "local-ids.json"
if not IDS.exists():
    sys.exit(f"missing {IDS}; copy local-ids.example.json and fill it")
ids = json.loads(IDS.read_text())
creds = ids["credentials"]
missing = [k for k, v in {**ids["airtable"], **{k: c["id"] for k, c in creds.items()}}.items() if "REPLACE" in str(v) or not v]
if missing:
    print("warning: still placeholders for", ", ".join(missing))


def cred_for(node):
    t = node["type"]; p = node["parameters"]; name = node["name"]
    if t == "n8n-nodes-base.airtable":
        return "airtableOAuth2Api", creds["airtable"]
    if t == "n8n-nodes-base.slack":
        return "slackApi", creds["slack"]
    if t == "n8n-nodes-base.httpRequest" and p.get("nodeCredentialType") == "slackApi":
        return "slackApi", creds["slack"]
    if t == "n8n-nodes-base.httpRequest" and p.get("genericAuthType") == "httpHeaderAuth":
        if name.startswith("GEMINI"):
            return "httpHeaderAuth", creds["gemini"]
        return "httpHeaderAuth", creds["groq"]          # GROQ GENERATE / GROQ CLASSIFY / GROQ REWRITE
    if t.endswith("tavily.tavily"):
        return "tavilyApi", creds["tavily"]
    if t == "n8n-nodes-base.linkedIn":
        return "linkedInOAuth2Api", creds["linkedin"]
    return None


for fname in ["A-preparer", "B-resolver", "C-reminders", "D-error-handler"]:
    wf = json.loads((ROOT / "workflows" / f"{fname}.json").read_text())
    for n in wf["nodes"]:
        p = n["parameters"]
        if "base" in p and isinstance(p["base"], dict):
            p["base"]["value"] = ids["airtable"]["base_id"]
            tbl = p["table"]["value"]
            p["table"]["value"] = ids["airtable"]["topics_table_id"] if "TOPICS" in tbl else ids["airtable"]["profile_table_id"] if "PROFILE" in tbl else tbl
        c = cred_for(n)
        if c:
            n["credentials"] = {c[0]: {"id": c[1]["id"], "name": c[1]["name"]}}
    text = json.dumps(wf, ensure_ascii=False, indent=2) + "\n"
    slack = ids.get("slack", {})
    text = text.replace("C_APPROVALS_REPLACE_ME", slack.get("approvals_channel_id", "C_APPROVALS_REPLACE_ME"))
    text = text.replace("C_ALERTS_REPLACE_ME", slack.get("alerts_channel_id", "C_ALERTS_REPLACE_ME"))
    tz = ids.get("timezone", "UTC")
    for q in ('"UTC"', "'UTC'", "(UTC)"):
        text = text.replace(q, q.replace("UTC", tz))
    out = RAW / f"{fname}.local.json"
    out.write_text(text)
    print("wrote", out.relative_to(ROOT))
