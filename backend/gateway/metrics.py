from collections import Counter

_counts: Counter[tuple[str, tuple[tuple[str, str], ...]]] = Counter()


def inc(name: str, **labels: str) -> None:
    _counts[(name, tuple(sorted(labels.items())))] += 1


def render() -> str:
    lines = [
        "# HELP gateway_events_total Gateway pipeline events.",
        "# TYPE gateway_events_total counter",
    ]
    for (name, labels), value in sorted(_counts.items()):
        label_text = ",".join(f'{key}="{_escape(label)}"' for key, label in labels)
        suffix = "{" + label_text + "}" if label_text else ""
        lines.append(f"{name}{suffix} {value}")
    if len(lines) == 2:
        lines.append('gateway_events_total{event="none"} 0')
    return "\n".join(lines) + "\n"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "")


def reset() -> None:
    _counts.clear()
