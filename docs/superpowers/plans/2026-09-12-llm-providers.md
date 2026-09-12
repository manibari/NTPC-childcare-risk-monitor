# LLM provider implementation plan

Goal: local Anthropic and production Bedrock share OpenAI Chat Completions requests and tool handling.
Architecture: app/llm.py owns environment configuration and client construction. app/agent.py owns provider-neutral read-only tool execution. Production workflow writes Bedrock configuration into the remote .env, preserving the legacy secret-name fallback.

- [x] Add config and HTTP transport tests for both providers, credentials, tool rounds and invalid configuration; observe failure.
- [x] Implement config/client and migrate AgentService to OpenAI messages/tools, preserving read-only tools and audit logging.
- [x] Wire dependencies, Compose and workflow; production uses bedrock_openai and us-west-2, CD model is explicitly openai.gpt-5.6-luna per user instruction.
- [x] Update .env.example, README.md, AGENTS.md, CLAUDE.md and deployment documentation.
- [x] Run focused tests, full pytest, workflow shell/Compose validation, and review the final diff. Live model verification requires deployment credentials and an enabled model.
