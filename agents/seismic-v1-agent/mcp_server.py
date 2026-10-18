from pathlib import Path
import json
from mcp.server import MCPServer
from runtime import invoke

ROOT = Path(__file__).resolve().parent
mcp = MCPServer('Personal Agent')

@mcp.tool()
def preview_model(*, config: str = 'config.json', output: str = 'preview_output') -> dict:
    '使用二维成像 V1 的真实建模和绘图函数，在新的输出目录绘制模型、观测系统与子波预览。'
    return invoke(ROOT, 'preview_model', {'config': config, 'output': output})

@mcp.tool()
def move_anomaly(*, source: str = 'config.json', output: str = 'modified_config.json', x: int, z: int) -> dict:
    '在新 JSON 文件中移动二维自检模型的单网格密度异常；保留原配置。'
    return invoke(ROOT, 'move_anomaly', {'source': source, 'output': output, 'x': x, 'z': z})

@mcp.resource('agent://capabilities')
def capabilities() -> str:
    return (ROOT / 'agent.json').read_text(encoding='utf-8')

@mcp.prompt()
def usage() -> str:
    return '仅调用已注册能力；缺失能力请向 Builder 提交升级请求。'

if __name__ == '__main__':
    mcp.run()
