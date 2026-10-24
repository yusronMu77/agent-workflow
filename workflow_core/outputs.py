"""Bounded views over immutable local tool output; no shell runner or provider claims."""
import json
from pathlib import Path
import re
import uuid

from .project import Rejected, digest, plain_path

LIMIT = 32_000_000
DIAGNOSTIC = re.compile(r'error|fail|exception|traceback|warning|fatal', re.I)


def wire(data):
    # Match the CLI's actual JSON encoding, including its final newline.
    return (json.dumps(data, ensure_ascii=True) + '\n').encode('utf-8')


def bounded_view(raw, metadata, budget, start=1, end=None, find=None, column=None):
    if not 2048 <= budget <= 64000:
        raise Rejected('Output view budget must be 2048..64000 bytes')
    text = raw.decode('utf-8-sig')
    lines = text.splitlines()
    if column is not None:
        if find is not None or end not in (None, start):
            raise Rejected('Column pagination requires one line and no --find')
        if not 1 <= start <= len(lines) or not 1 <= column <= len(lines[start - 1]) + 1:
            raise Rejected('Invalid line/column cursor')
        value = lines[start - 1][column - 1:]
        # Columns count Unicode characters, never partial UTF-8 bytes.
        chunk = value.encode('utf-8')[:700].decode('utf-8', errors='ignore')
        result = dict(metadata, total_lines=len(lines), matched_lines=1, lines=[],
                      complete=False, delivered_bytes=0, next_column=None,
                      content_kind='untrusted_tool_output', native_tokens=None)
        while True:
            next_column = column + len(chunk) if len(chunk) < len(value) else None
            result.update(next_column=next_column,
                          complete=len(lines) == 1 and column == 1 and next_column is None,
                          lines=[dict(line=start, column_start=column, text=chunk,
                                      clipped=column != 1 or next_column is not None)])
            for _ in range(8):
                size = len(wire(result))
                if size == result['delivered_bytes']:
                    break
                result['delivered_bytes'] = size
            if len(wire(result)) <= budget:
                return result
            if not chunk:
                raise Rejected('Metadata exceeds view budget')
            chunk = chunk[:len(chunk) // 2]
            if not chunk and value:
                raise Rejected('Metadata exceeds view budget')
    if start < 1 or (end is not None and end < start):
        raise Rejected('Invalid inclusive line range')
    end = len(lines) if end is None else min(end, len(lines))
    indices = range(start - 1, end)
    literal_pattern = re.compile(re.escape(find), re.I) if find is not None else None
    matching = [i for i in indices if literal_pattern is None or literal_pattern.search(lines[i])]
    if find is None:
        # Diagnostic lines first, then head/tail. This is a preview, never a complete report.
        ordered = list(dict.fromkeys([i for i in matching if DIAGNOSTIC.search(lines[i])]
                                     + matching[:8] + matching[-12:]))
    else:
        ordered = matching
    result = dict(metadata, total_lines=len(lines), matched_lines=len(matching), lines=[],
                  complete=False, delivered_bytes=0,
                  content_kind='untrusted_tool_output', native_tokens=None)
    chosen = []
    for i in ordered:
        value = lines[i]
        clipped = len(value.encode('utf-8')) > 700
        column = 1
        if clipped:
            match = (literal_pattern or DIAGNOSTIC).search(value)
            match_at = match.start() if match else 0
            offset = max(0, match_at - 60)
            column = offset + 1
            value = value[offset:]
            value = value.encode('utf-8')[:700].decode('utf-8', errors='ignore')
        row = dict(line=i + 1, column_start=column, text=value, clipped=clipped)
        candidate = sorted(chosen + [row], key=lambda x: x['line'])
        result['lines'] = candidate
        if len(wire(result)) + 64 > budget:
            result['lines'] = sorted(chosen, key=lambda x: x['line'])
            continue
        chosen.append(row)
    result['complete'] = len(chosen) == len(lines) and not any(x['clipped'] for x in chosen)
    # The count itself changes JSON length; converge before persisting/printing.
    for _ in range(8):
        size = len(wire(result))
        if result['delivered_bytes'] == size:
            break
        result['delivered_bytes'] = size
    if len(wire(result)) > budget:
        raise Rejected('Metadata exceeds view budget')
    return result


class Outputs:
    def __init__(self, engine):
        self.engine = engine

    def directory(self, task, key):
        if not re.fullmatch(r'[a-f0-9]{32}', key):
            raise Rejected('Invalid output key')
        return plain_path(self.engine.directory(task) / 'outputs' / key)

    def capture(self, task, source, exit_code=None, budget=6000, find=None):
        spec = self.engine.guard(task)["spec"]
        path = self.engine.project.resolve(source)
        if path.name.casefold() in ('agents.md', 'claude.md', 'skill.md'):
            raise Rejected('Mandatory instructions must be read completely, not captured as output')
        if path.stat().st_size > LIMIT:
            raise Rejected('Output exceeds32MB; split the source explicitly')
        raw = path.read_bytes()
        if len(raw) > LIMIT:
            raise Rejected('Output grew beyond32MB')
        try:
            raw.decode('utf-8-sig')  # Validate before creating owned files.
        except UnicodeDecodeError:
            raise Rejected(f'Output {source} is not UTF-8 text (UTF-16/binary); re-export as UTF-8 before capture')
        key = uuid.uuid4().hex
        artifact = self.engine.project.resolve(spec['output'])
        output_sha256 = digest(artifact.read_bytes()) if artifact.is_file() and artifact.stat().st_size <= LIMIT else None
        metadata = dict(key=key, source=source, sha256=digest(raw), raw_bytes=len(raw), exit_code=exit_code,
                        output_sha256=output_sha256)
        result = bounded_view(raw, metadata, budget, find=find)
        directory = self.directory(task, key)
        directory.mkdir(parents=True, exist_ok=False)
        with (directory / 'raw.txt').open('xb') as stream:
            stream.write(raw)
        with (directory / 'manifest.json').open('x', encoding='utf-8') as stream:
            json.dump(metadata, stream)
        # Transactional record binds the raw file AND its metadata to prevent manifest tampering.
        with self.engine.store.tx() as db:
            self.engine.store.event(db, task, 'output_captured', dict(metadata, delivered_bytes=result['delivered_bytes']))
        return result

    def read(self, task, key, budget=6000, start=1, end=None, find=None, column=None):
        self.engine.store.task(task)  # Allow inspection after completion, without replaying writes.
        directory = self.directory(task, key)
        manifest = plain_path(directory / 'manifest.json')
        metadata = json.loads(manifest.read_text(encoding='utf-8'))
        records = self._events(task, 'output_captured')
        expected = next((x for x in records if x['key'] == key), None)
        if expected is None or any(expected.get(k) != v for k, v in metadata.items()) or set(metadata) != set(expected) - {'delivered_bytes'}:
            raise Rejected('Output metadata is unrecorded or changed')
        path = plain_path(directory / 'raw.txt')
        if path.stat().st_size > LIMIT:
            raise Rejected('Archived output exceeds32MB')
        raw = path.read_bytes()
        if digest(raw) != metadata['sha256'] or len(raw) != metadata['raw_bytes']:
            raise Rejected('Archived output hash changed')
        result = bounded_view(raw, metadata, budget, start, end, find, column)
        with self.engine.store.tx() as db:
            self.engine.store.event(db, task, 'output_read', dict(key=key, delivered_bytes=result['delivered_bytes']))
        return result

    def _events(self, task, kind):
        return [json.loads(row['payload']) for row in self.engine.store.connection.execute(
            'SELECT payload FROM events WHERE task_id=? AND kind=? ORDER BY created, rowid', (task, kind))]

    def metrics(self, task):
        self.engine.store.task(task)
        captures = self._events(task, 'output_captured')
        reads = self._events(task, 'output_read')
        raw = sum(x['raw_bytes'] for x in captures)
        delivered = sum(x['delivered_bytes'] for x in captures + reads)
        packs = self._events(task, 'context_prepared')
        return dict(captures=len(captures), reads=len(reads), raw_payload_bytes=raw,
                    delivered_view_bytes=delivered, net_payload_bytes_proxy=raw - delivered,
                    pack_bytes=sum(x['bytes'] for x in packs),
                    pack_count=len(packs),
                    policy_bytes=(sum(x['policy_bytes'] for x in packs)
                                  if all('policy_bytes' in x for x in packs) else None),
                    policy_ref_bytes=(sum(x['policy_ref_bytes'] for x in packs)
                                      if all('policy_ref_bytes' in x for x in packs) else None),
                    native_tokens=None, provider_cost=None,
                    caveat='Bytes for capture/read only; includes repeat reads and JSON overhead. Not total context, tokenizer, replay or billing savings.')
