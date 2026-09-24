def assert_branch_allowed(branch: str, prefix: str) -> None:
    if not branch.startswith(prefix):
        raise ValueError(f"branch {branch!r} is outside the agent's prefix {prefix!r}")


def assert_labels_allowed(labels: list[str], namespace: str) -> None:
    foreign = [label for label in labels if not label.startswith(namespace)]
    if foreign:
        raise ValueError(f"labels outside {namespace}: {', '.join(foreign)}")
