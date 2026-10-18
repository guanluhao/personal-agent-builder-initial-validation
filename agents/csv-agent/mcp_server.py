from pathlib import Path
import json
from mcp.server import MCPServer
from runtime import invoke

ROOT = Path(__file__).resolve().parent
mcp = MCPServer('Personal Agent')

@mcp.tool()
def summarize_csv(input: str = 'data.csv', column: str = 'amount', output: str = 'summary.json') -> dict:
    '汇总项目副本中 CSV 文件的数值列，输出 JSON。'
    return invoke(ROOT, 'summarize_csv', {'input': input, 'column': column, 'output': output})

@mcp.resource('agent://capabilities')
def capabilities() -> str:
    return (ROOT / 'agent.json').read_text(encoding='utf-8')

@mcp.prompt()
def usage() -> str:
    return '仅调用已注册能力；缺失能力请向 Builder 提交升级请求。'

if __name__ == '__main__':
    mcp.run()
