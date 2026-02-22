from agent.tool_protocol import registry
from tools.github_tool import GitHubTool
from tools.serper_tool import SerperSearchTool
from tools.playwright_tool import PlaywrightTool
from tools.news_tool import SerperNewsTool
from tools.community_tool import CommunityActivityTool

registry.register(GitHubTool())
registry.register(SerperSearchTool())
registry.register(PlaywrightTool())
registry.register(SerperNewsTool())
registry.register(CommunityActivityTool())
# EmailPipeline NOT registered — orchestrator calls it directly
# HunterIoTool NOT registered — called internally by EmailPipeline
