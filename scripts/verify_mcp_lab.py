#!/usr/bin/env python3
"""Read an existing disposable workspace fixture through a real MCP subprocess.

Run only against the fixture from verify_workspace_lab.py; requires optional MCP
requirements and PANEL_TOKEN in the environment. Never prints credentials.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile

from mcp import Client, StdioServerParameters

ROOT = Path(__file__).resolve().parents[1]
TOOLS = {
    'list_servers', 'list_tasks', 'get_task', 'observe_task', 'read_task_log',
    'list_task_files', 'read_task_text', 'list_reported_runs',
}


async def verify(args):
    # Exercise the same generated configuration a new MCP host would consume.
    token = os.environ['PANEL_TOKEN']
    with tempfile.TemporaryDirectory(prefix='mcp-config-', dir=ROOT / 'data') as local:
        token_path = Path(local) / 'fixture-token'
        with token_path.open('x', encoding='utf-8') as stream:
            os.chmod(token_path, 0o600)
            stream.write(token)
        command = [sys.executable, str(ROOT / 'scripts/panellab_mcp.py'), '--url', args.url,
                   '--token-file', str(token_path)]
        configured = subprocess.run(command + ['--print-config'], cwd=local,
                                    capture_output=True, text=True, timeout=10)
        assert configured.returncode == 0, 'MCP configuration generation failed'
        assert token not in configured.stdout + configured.stderr
        host = json.loads(configured.stdout)['mcpServers']['panellab']
        checked = subprocess.run([host['command'], *host['args'], '--check'], cwd=local,
                                 capture_output=True, text=True, timeout=40)
        assert checked.returncode == 0, 'MCP connection check failed'
        check = json.loads(checked.stdout)
        assert check['ok'] is True and check['server_count'] >= 1
        assert token not in checked.stdout + checked.stderr
        parameters = StdioServerParameters(command=host['command'], args=host['args'], env={}, cwd=local)
        async with Client(parameters, read_timeout_seconds=45) as client:
            catalog = await client.list_tools()
            assert {tool.name for tool in catalog.tools} == TOOLS
            assert all(tool.description and tool.input_schema for tool in catalog.tools)
            assert client.instructions

            async def read(name, values=None):
                result = await client.call_tool(name, values or {})
                assert not result.is_error, f'MCP fixture call failed: {name}'
                assert isinstance(result.structured_content, dict), name
                return result.structured_content

            servers = await read('list_servers')
            assert any(row['id'] == args.server_id for row in servers['items'])
            assert all(set(row) <= {'id', 'name', 'host', 'port', 'username'} for row in servers['items'])
            tasks = await read('list_tasks', {'query': '训练现场', 'kind': 'training'})
            assert any(row['id'] == args.task_id for row in tasks['items'])
            task = await read('get_task', {'task_id': args.task_id})
            assert task['root_path'] == args.root and task['server_id'] == args.server_id
            observed = await read('observe_task', {'task_id': args.task_id})
            assert observed['workload_id'] == task['id']
            assert observed['process_status'] == 'running'
            assert observed['context']['workload']['process_identity'] == task['process_identity']
            assert observed['context']['server']['id'] == args.server_id
            assert observed['sampling']['boot_id'] and observed['sampling']['monotonic_seconds'] > 0
            assert observed['observed_at'] and observed['system']['cpu']['ticks']
            log = await read('read_task_log', {'task_id': args.task_id})
            assert 'fixture_step=' in log['text'] and log['path'] == task['log_path']
            files = await read('list_task_files', {'task_id': args.task_id})
            assert any(row['name'] == 'outputs' for row in files['entries'])
            outputs = await read('list_task_files', {'task_id': args.task_id, 'path': args.root + '/outputs'})
            assert any(row['name'] == 'result.json' for row in outputs['entries'])
            text = await read('read_task_text', {'task_id': args.task_id, 'path': args.root + '/outputs/result.json'})
            assert json.loads(text['text']) == {'fixture': True, 'score': 0.42}
            large = await read('read_task_text', {'task_id': args.task_id, 'path': args.root + '/large.log'})
            assert large['truncated'] and 'TAIL_MARKER' in large['text']
            runs = await read('list_reported_runs', {'task_id': args.task_id})
            assert runs['items'] and all(row['source'] == 'reported' for row in runs['items'])
            for path in ('/etc/passwd', args.root + '/outside-link/passwd', args.root + '/named-pipe'):
                denied = await client.call_tool('read_task_text', {'task_id': args.task_id, 'path': path})
                assert denied.is_error, 'MCP did not preserve remote file boundary'
            absent = await client.call_tool('get_task', {'task_id': 2147483647})
            assert absent.is_error
    print('PASS: MCP generated config/check, stdio discovery, shared task identity, real SSH observation, bounded logs/files, reported runs and path refusal')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--task-id', type=int, required=True)
    parser.add_argument('--server-id', type=int, required=True)
    parser.add_argument('--root', required=True)
    args = parser.parse_args()
    asyncio.run(verify(args))


if __name__ == '__main__':
    main()
