"""Stateful Docker simulator. It never invokes Docker or any service."""
import copy
import json
import subprocess


class FakeDocker:
    def __init__(self, old):
        self.containers = {old['Id']: copy.deepcopy(old)}
        self.calls = []
        self.requests = []
        self.fail_create = False
        self.fail_start = False
        self.fail_connect = set()
        self.after_rename = None
        self.after_create = None
        self.after_start = None
        self.new_ids = []
        self.manual_stops = set()
        self.armed = {old['Id']}
        self.age = {old['Id']: 10}
        self.fail_policy = None
        self.after_policy_update = None
        self.daemon_events = []
        self.offline_checks = []

    def offline_guard(self, roots, exclude=()):
        """Verify this isolated daemon's mount users; never inspect the host's /proc.

        Native processes are outside the fake daemon. Signal-test workers have
        exited before recovery, so the remaining fixture writers are containers.
        As in the real guard, any running user of an overlapping mount blocks it.
        """
        from pathlib import Path
        roots = [Path(root) for root in roots]
        self.offline_checks.append(tuple(map(str, roots)))
        for container in self.containers.values():
            if container['Id'] in exclude or not container['State'].get('Running'):
                continue
            for mount in container.get('Mounts', []):
                source = Path(mount['Source'])
                if any(source == root or source in root.parents or root in source.parents for root in roots):
                    raise RuntimeError('simulated mount still in use: ' + container['Id'])

    def lookup(self, name):
        for identifier, container in self.containers.items():
            if name == identifier or name.lstrip('/') == container['Name'].lstrip('/'):
                return container
        raise subprocess.CalledProcessError(1, ['docker', 'inspect', name], stderr='Error: No such object: ' + name)

    def __call__(self, *args):
        self.calls.append(args)
        if args[0] in ('info', 'pull'):
            return ''
        if args[0] == 'ps':
            return '\n'.join(self.containers)
        if args[:2] == ('container', 'inspect'):
            return json.dumps([self.lookup(name) for name in args[2:]])
        if args[:2] == ('image', 'inspect'):
            return json.dumps([{'Id': 'new:image'}])
        if args[0] == 'stop':
            container = self.lookup(args[1])
            container['State'].update(Running=False, Status='exited')
            self.manual_stops.add(container['Id'])
            return ''
        if args[0] == 'start':
            if args[1] in self.new_ids and self.fail_start:
                raise subprocess.CalledProcessError(1, args, stderr='start failed')
            container = self.lookup(args[1])
            container['State'].update(Running=True, Status='running')
            self.manual_stops.discard(container['Id'])
            self.age.setdefault(container['Id'], 0)
            if args[1] in self.new_ids and self.after_start:
                self.after_start(container)
            return ''
        if args[0] == 'rename':
            container = self.lookup(args[1])
            if any(c['Id'] != container['Id'] and c['Name'].lstrip('/') == args[2] for c in self.containers.values()):
                raise subprocess.CalledProcessError(1, args, stderr='name conflict')
            container['Name'] = '/' + args[2]
            if self.after_rename:
                hook, self.after_rename = self.after_rename, None
                hook(container)
            return ''
        if args[:2] == ('network', 'disconnect'):
            self.lookup(args[3])['NetworkSettings']['Networks'].pop(args[2], None)
            return ''
        raise AssertionError('Unexpected Docker command: ' + repr(args))

    def advance(self, seconds):
        """Simplified >=10s activation threshold, matching Docker's documented restart-policy rule."""
        for identifier, container in self.containers.items():
            if container['State'].get('Running'):
                self.age[identifier] = self.age.get(identifier, 0) + seconds
                if self.age[identifier] >= 10:
                    self.armed.add(identifier)

    def daemon_restart(self):
        """No real daemon. always revives manual stops; unless-stopped preserves them; on-failure does not."""
        eligible = []
        for identifier, container in self.containers.items():
            policy = container.get('HostConfig', {}).get('RestartPolicy', {}).get('Name', 'no')
            if identifier in self.armed and (policy == 'always' or (policy == 'unless-stopped' and identifier not in self.manual_stops)):
                eligible.append(identifier)
            container['State'].update(Running=False, Status='exited')
        for identifier in eligible:
            self.containers[identifier]['State'].update(Running=True, Status='running')
        self.daemon_events.append({'running': self.running_ids(), 'conflicts': self.resource_conflicts()})
        return self.running_ids()

    def running_ids(self):
        return {identifier for identifier, c in self.containers.items() if c['State'].get('Running')}

    def resource_conflicts(self):
        # Observe volume/port conflicts rather than hiding them by rejecting the second instance.
        # Docker permits shared volumes; real port-allocation failure is left to the integration suite.
        from pathlib import Path
        running = [c for c in self.containers.values() if c['State'].get('Running')]
        conflicts = []
        for index, left in enumerate(running):
            for right in running[index + 1:]:
                for a in left.get('Mounts', []):
                    for b in right.get('Mounts', []):
                        if a.get('RW') and b.get('RW') and (Path(a['Source']) == Path(b['Source']) or Path(a['Source']) in Path(b['Source']).parents or Path(b['Source']) in Path(a['Source']).parents):
                            conflicts.append(('volume', left['Id'], right['Id']))
                ports_a = {p.get('HostPort') for values in left['HostConfig'].get('PortBindings', {}).values() for p in values or [] if p.get('HostPort')}
                ports_b = {p.get('HostPort') for values in right['HostConfig'].get('PortBindings', {}).values() for p in values or [] if p.get('HostPort')}
                if ports_a & ports_b:
                    conflicts.append(('port', left['Id'], right['Id']))
        return conflicts

    def engine(self):
        fake = self
        class Engine:
            def request(self, method, path, data=None):
                fake.requests.append((method, path, copy.deepcopy(data)))
                if path.startswith('/containers/') and path.endswith('/update'):
                    container = fake.lookup(path.split('/')[2])
                    policy = copy.deepcopy(data['RestartPolicy'])
                    if fake.fail_policy and fake.fail_policy(container['Id'], policy):
                        raise RuntimeError('restart policy update rejected')
                    container['HostConfig']['RestartPolicy'] = policy
                    if fake.after_policy_update:
                        hook, fake.after_policy_update = fake.after_policy_update, None
                        hook(container, policy)
                    return {}
                if path.startswith('/containers/create'):
                    if fake.fail_create:
                        raise RuntimeError('create failed')
                    identifier = 'new-id' if not fake.new_ids else 'new-id-' + str(len(fake.new_ids) + 1)
                    fake.new_ids.append(identifier)
                    config = {key: copy.deepcopy(value) for key, value in data.items()
                              if key not in ('HostConfig', 'NetworkingConfig')}
                    fake.containers[identifier] = {'Id': identifier, 'Image': data['Image'], 'Name': '/app',
                        'State': {'Running': False, 'Status': 'created'}, 'Config': config,
                        'HostConfig': copy.deepcopy(data['HostConfig']), 'Mounts': copy.deepcopy(next(iter(fake.containers.values()))['Mounts']),
                        'NetworkSettings': {'Networks': copy.deepcopy(data['NetworkingConfig']['EndpointsConfig'])}}
                    if fake.after_create:
                        fake.after_create(fake.containers[identifier])
                    return {'Id': identifier}
                if path.endswith('/connect'):
                    network = path.split('/')[2]
                    if network in fake.fail_connect:
                        raise RuntimeError('reconnect failed: ' + network)
                    fake.lookup(data['Container'])['NetworkSettings']['Networks'][network] = copy.deepcopy(data['EndpointConfig'])
                    return {}
                raise AssertionError('Unexpected API request: ' + path)
        return Engine
