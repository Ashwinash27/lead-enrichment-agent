from agent.tool_protocol import registry
from tools.github_tool import GitHubTool
from tools.search_tool import WebSearchTool
from tools.playwright_tool import PlaywrightTool

registry.register(GitHubTool())
registry.register(WebSearchTool())
registry.register(PlaywrightTool())
