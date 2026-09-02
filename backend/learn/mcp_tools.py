class MCPTool:

    def __init__(self, session, tool_name):
        self.session = session
        self.tool_name = tool_name

    async def __call__(self, **kwargs):
        result = await self.session.call_tool(
            self.tool_name,
            kwargs,
        )

        text_parts = [
            block.text
            for block in result.content
            if block.type == "text"
        ]

        return "\n".join(text_parts)

    def mcp_tool_to_openai_tool(tool) -> dict:
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": tool.inputSchema,
            },
        }