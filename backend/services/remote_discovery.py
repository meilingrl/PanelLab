"""Optional read-only local Tailscale discovery, fixed argv and bounded I/O."""
import json
import os
import selectors
import shutil
import subprocess
import time


def unavailable(reason):
    return {'available': False, 'reason': reason, 'devices': []}


def discover_tailscale():
    binary = shutil.which('tailscale')
    if not binary:
        return unavailable('Tailscale CLI is not installed')
    process = None
    try:
        process = subprocess.Popen([binary, 'status', '--json'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        output = bytearray()
        total = 0
        deadline = time.monotonic() + 5
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            selector.register(process.stderr, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return unavailable('Tailscale status timed out')
                for key, _ in selector.select(min(remaining, 0.1)):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    total += len(chunk)
                    if total > 512 * 1024:
                        return unavailable('Tailscale status exceeded output limit')
                    if key.fileobj is process.stdout:
                        output.extend(chunk)
            remaining = deadline - time.monotonic()
            if remaining <= 0 or process.wait(timeout=remaining) != 0:
                return unavailable('Tailscale is unavailable or not running')
        data = json.loads(output)
        if not isinstance(data, dict) or data.get('BackendState') != 'Running':
            return unavailable('Tailscale is not running')
        peers = data.get('Peer', {})
        if not isinstance(peers, dict):
            return unavailable('Invalid Tailscale status')
        devices = []
        for peer in list(peers.values())[:200]:
            if not isinstance(peer, dict):
                continue
            devices.append({'name': str(peer.get('HostName', ''))[:255],
                            'dns_name': str(peer.get('DNSName', ''))[:255],
                            'addresses': [str(ip)[:64] for ip in peer.get('TailscaleIPs', [])[:16]],
                            'online': peer.get('Online') is True})
        return {'available': True, 'reason': None, 'devices': devices}
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return unavailable('Tailscale status is unavailable or invalid')
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()
            process.stderr.close()
