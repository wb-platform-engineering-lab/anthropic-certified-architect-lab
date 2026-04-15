import anthropic
import json
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic()

TICKET = "My billing amount this month is wrong. I was charged €450 but my plan should be €300."

# --- Approach A: Ask for JSON in the prompt ---
# This is what Resolve did before the Chapter 3 incident.
def approach_a_ask_for_json():
    print("\n=== Approach A: Ask for JSON in prompt ===")
    print("(Run this 3 times — notice the output varies)")

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        system=(
            "You are a support ticket classifier. "
            "Always respond with a JSON object with these exact fields: "
            "decision (string: 'auto_resolve' or 'escalate'), "
            "confidence (float 0.0-1.0), "
            "reason (string)."
            # The model MAY comply. But it is not required to.
            # A warm preamble, a markdown code block, or an explanation
            # can all appear instead — and will break any downstream parser.
        ),
        messages=[{"role": "user", "content": TICKET}]
    )

    raw = response.content[0].text
    print(f"Raw output:\n{raw}")

    try:
        # This will fail if the model wrapped the JSON in markdown
        data = json.loads(raw)
        print(f"\n✓ Parsed successfully: {data}")
    except json.JSONDecodeError as e:
        print(f"\n✗ Parse failed: {e}")
        print("→ The model gave you something other than raw JSON.")
        print("→ In production, this ticket was silently dropped or misrouted.")

# --- Approach B: Require structure via tool_use ---
# The model MUST call this tool to end the turn.
# There is no valid path where it returns unstructured text.
def approach_b_require_via_tool():
    print("\n=== Approach B: Require via tool_use ===")

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        tools=[{
            "name": "classify_ticket",
            "description": (
                "Classify the support ticket and determine the resolution path. "
                "Call this tool for every ticket — it is the only valid way to complete ticket processing."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "decision": {
                        "type": "string",
                        "enum": ["auto_resolve", "escalate"],
                        "description": "Whether to auto-resolve or escalate to a human agent."
                    },
                    "confidence": {
                        "type": "number",
                        "description": "Confidence score between 0.0 and 1.0."
                    },
                    "reason": {
                        "type": "string",
                        "description": "One-sentence explanation of the decision."
                    },
                    "escalation_team": {
                        "type": "string",
                        "enum": ["billing", "technical", "account_management"],
                        "description": "Required if decision is 'escalate'. The team to route to."
                    }
                },
                "required": ["decision", "confidence", "reason"]
            }
        }],
        # Force the model to use this specific tool
        tool_choice={"type": "tool", "name": "classify_ticket"},
        messages=[{"role": "user", "content": TICKET}]
    )

    print(f"stop_reason: {response.stop_reason}")

    for block in response.content:
        if block.type == "tool_use":
            print(f"\n✓ Tool called: {block.name}")
            print(f"Structured output: {json.dumps(block.input, indent=2)}")

            # This will never fail — the schema was enforced by the API
            decision = block.input["decision"]
            confidence = block.input["confidence"]
            reason = block.input["reason"]
            escalation_team = block.input.get("escalation_team")

            print(f"\nRouting decision: {decision} (confidence: {confidence:.0%})")
            if escalation_team:
                print(f"Escalate to: {escalation_team}")
            print(f"Reason: {reason}")


# Run both
approach_a_ask_for_json()
approach_b_require_via_tool()