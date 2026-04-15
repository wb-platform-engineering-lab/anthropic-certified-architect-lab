import anthropic
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic()

response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=10,
    messages=[
        {
            "role": "user",
            "content": "A customer submitted a support ticket saying their invoice is wrong. In one sentence, what should a support agent do first?"
        }
    ]
)

print("=== Full response object ===")
print(f"id:           {response.id}")
print(f"model:        {response.model}")
print(f"stop_reason:  {response.stop_reason}")
print(f"usage:        input={response.usage.input_tokens}, output={response.usage.output_tokens}")
print()
print("=== Content blocks ===")
for block in response.content:
    print(f"type: {block.type}")
    print(f"text: {block.text}")