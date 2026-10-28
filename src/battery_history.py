"""Bounded battery history and conservative discharge estimates (no OS access)."""
import json
import logging
import math
import os
from pathlib import Path
import time

RETENTION = 7 * 86400


def _valid(sample):
    return (isinstance(sample, dict) and type(sample.get('t')) in (int, float)
            and math.isfinite(sample['t']) and isinstance(sample.get('id'), str)
            and isinstance(sample.get('name'), str)
            and type(sample.get('level')) is int and 0 <= sample['level'] <= 100
            and isinstance(sample.get('source'), str)
            and (sample.get('charging') is None or type(sample['charging']) is bool))


class BatteryHistory:
    def __init__(self, path):
        self.path = Path(path)
        self._samples = []
        self._last_prune = time.time()
        try:
            with self.path.open(encoding='utf-8', errors='replace') as stream:
                for line in stream:
                    try:
                        sample = json.loads(line)
                        if _valid(sample):
                            self._samples.append(sample)
                    except ValueError:
                        continue
        except OSError:
            pass
        self._samples.sort(key=lambda sample: sample['t'])
        self.prune()

    def _save(self):
        temporary = self.path.with_suffix(self.path.suffix + '.tmp')
        try:
            with temporary.open('w', encoding='utf-8') as stream:
                for sample in self._samples:
                    stream.write(json.dumps(sample, ensure_ascii=False) + '\n')
            os.replace(temporary, self.path)
        except OSError:
            logging.exception('Cannot save battery history')

    def prune(self, now=None):
        now = time.time() if now is None else now
        self._samples = [sample for sample in self._samples if sample['t'] >= now - RETENTION]
        self._last_prune = now
        self._save()

    def record(self, device_id, name, level, source, charging, now=None):
        now = time.time() if now is None else now
        sample = dict(t=now, id=device_id, name=name, level=level, source=source, charging=charging)
        if not _valid(sample):
            return False
        if now - self._last_prune >= 3600:
            self.prune(now)
        previous = next((s for s in reversed(self._samples) if s['id'] == device_id), None)
        if (previous and all(previous[field] == sample[field] for field in ('level', 'source', 'charging'))
                and now - previous['t'] < 300):
            return False
        self._samples.append(sample)
        # Append one line; the whole file is rewritten only when pruning.
        try:
            with self.path.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(sample, ensure_ascii=False) + '\n')
        except OSError:
            logging.exception('Cannot append battery history')
        return True

    def samples(self, device_id, since=None):
        return [dict(sample) for sample in self._samples if sample['id'] == device_id
                and (since is None or sample['t'] >= since)]


def estimate_remaining(samples):
    """Minutes to zero from the latest noncharging, nonincreasing six-hour run."""
    now = time.time()
    run = []
    for sample in sorted(samples, key=lambda sample: sample['t']):
        if not now - 21600 <= sample['t'] <= now:
            continue
        if sample.get('charging'):
            run = []
            continue
        # A rise, a provider change or a long unobserved gap starts a new run.
        if run and (sample['level'] > run[-1]['level']
                    or sample.get('source') != run[-1].get('source')
                    or sample['t'] - run[-1]['t'] > 1800):
            run = []
        run.append(sample)
    if len(run) < 2 or run[-1]['t'] - run[0]['t'] < 900 or run[0]['level'] - run[-1]['level'] < 3:
        return None
    # Center the times for numerical stability with epoch timestamps.
    xs = [(sample['t'] - run[0]['t']) / 60 for sample in run]
    ys = [sample['level'] for sample in run]
    xmean, ymean = sum(xs) / len(xs), sum(ys) / len(ys)
    variance = sum((x - xmean) ** 2 for x in xs)
    slope = sum((x - xmean) * (y - ymean) for x, y in zip(xs, ys)) / variance
    return max(0, -(ymean + slope * (xs[-1] - xmean)) / slope) if slope < 0 else None
