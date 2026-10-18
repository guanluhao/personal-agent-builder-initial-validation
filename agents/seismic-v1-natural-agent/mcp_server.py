from pathlib import Path
import json
from mcp.server import MCPServer
from runtime import invoke

ROOT = Path(__file__).resolve().parent
mcp = MCPServer('Personal Agent')

@mcp.tool()
def preview_model(*, config: str = 'config.json', output: str = 'preview_output') -> dict:
    '调用二维 V1 的真实建模与绘图函数，预览当前配置，生成模型和观测系统 PNG/SVG 图片。'
    return invoke(ROOT, 'preview_model', {'config': config, 'output': output})

@mcp.tool()
def shift_anomaly_meters(*, source: str = 'config.json', output: str = 'shifted_config.json', left_m: int, down_m: int) -> dict:
    '把当前二维单网格密度异常按物理距离移动。left_m 是向左多少米，down_m 是向下多少米；从当前密度异常配置继续修改，原配置不覆盖。'
    return invoke(ROOT, 'shift_anomaly_meters', {'source': source, 'output': output, 'left_m': left_m, 'down_m': down_m})

@mcp.tool()
def extend_tunnel(*, source: str = 'tunnel_config.json', output: str = 'extended_tunnel_config.json', meters: int) -> dict:
    '把二维隧道示意配置中唯一的目标空腔向右延长 meters 米；只改右端物理边界，保留旧配置。不是完整隧道施工或波场计算。'
    return invoke(ROOT, 'extend_tunnel', {'source': source, 'output': output, 'meters': meters})

@mcp.resource('agent://capabilities')
def capabilities() -> str:
    return (ROOT / 'agent.json').read_text(encoding='utf-8')

@mcp.prompt()
def usage() -> str:
    return '仅调用已注册能力；缺失能力请向 Builder 提交升级请求。'

if __name__ == '__main__':
    mcp.run()
