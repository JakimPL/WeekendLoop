from typing import Final

BLOCKERS_QUERY: Final[str] = """
query($owner: String!, $name: String!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    issues(states: OPEN, first: 100, after: $endCursor) {
      nodes { number blockedBy(first: 50) { nodes { number state } } }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""
