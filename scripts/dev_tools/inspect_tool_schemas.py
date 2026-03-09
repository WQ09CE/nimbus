import asyncio
import json
from nimbus.core.agent import AgentOS

def main():
    agent = AgentOS()
    schemas = agent._registry.get_schemas("openai")
    print(json.dumps(schemas, indent=2))

if __name__ == "__main__":
    main()
