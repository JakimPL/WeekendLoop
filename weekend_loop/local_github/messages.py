from typing import Final

VERSION: Final[str] = "gh version local (Weekend Loop board at {root})"
UNSUPPORTED_COMMAND: Final[str] = "local gh: `gh {command}` is not available on the local board"
USAGE: Final[str] = "local gh: {message}"
NO_REPOSITORY: Final[str] = (
    "could not determine base repo: pass --repo, set GH_REPO, or run inside a clone of the board"
)
REPOSITORY_NOT_FOUND: Final[str] = (
    "GraphQL: Could not resolve to a Repository with the name '{slug}'. (repository)"
)
ISSUE_NOT_FOUND: Final[str] = (
    "GraphQL: Could not resolve to an issue or pull request with the number of {number}. "
    "(repository.issue)"
)
PULL_REQUEST_NOT_FOUND: Final[str] = (
    "GraphQL: Could not resolve to a PullRequest with the number of {number}. "
    "(repository.pullRequest)"
)
LABEL_NOT_FOUND: Final[str] = "'{label}' not found"
EDIT_FAILED: Final[str] = "failed to update {url}: {reason}"
ADD_LABELS_FAILED: Final[str] = "could not add label: {reason}"
TITLE_AND_BODY_REQUIRED: Final[str] = (
    "must provide `--title` and `--body` (or `--fill`) when not running interactively"
)
BODY_REQUIRED: Final[str] = "`--body` or `--body-file` required when not running interactively"
HEAD_REQUIRED: Final[str] = "could not determine the current branch; pass `--head`"
PULL_REQUEST_EXISTS: Final[str] = (
    'a pull request for branch "{head}" into branch "{base}" already exists:\n{url}'
)
BRANCH_MISSING: Final[str] = (
    "pull request create failed: GraphQL: Head sha can't be blank, Base sha can't be blank, "
    "Head ref must be a branch (createPullRequest)"
)
NO_COMMITS: Final[str] = (
    "pull request create failed: GraphQL: No commits between {base} and {head} (createPullRequest)"
)
LABEL_EXISTS: Final[str] = (
    'label with name "{name}" already exists; use `--force` to update its color and description'
)
UNKNOWN_JSON_FIELD: Final[str] = 'Unknown JSON field: "{field}"\nAvailable fields:\n{available}'
CLOSED_ISSUE: Final[str] = "✓ Closed issue {slug}#{number} ({title})"
REOPENED_ISSUE: Final[str] = "✓ Reopened issue {slug}#{number} ({title})"
CLOSED_PULL_REQUEST: Final[str] = "✓ Closed pull request {slug}#{number} ({title})"
LABEL_CREATED: Final[str] = '✓ Label "{name}" created in {slug}'
LABEL_UPDATED: Final[str] = '✓ Label "{name}" updated in {slug}'

API_FAILURE: Final[str] = "gh: {message} (HTTP {status})"
GRAPHQL_FAILURE: Final[str] = "gh: {message}"
DOCUMENTATION_URL: Final[str] = "https://docs.github.com/rest"
NOT_FOUND: Final[str] = "Not Found"
FORBIDDEN: Final[str] = "Resource not accessible by personal access token"
OBJECT_MISSING: Final[str] = "Object does not exist"
REFERENCE_EXISTS: Final[str] = "Reference already exists"
VALIDATION_FAILED: Final[str] = "Validation Failed"
NOT_AN_INTEGER: Final[str] = (
    "Invalid request.\n\nFor 'properties/{name}', \"{value}\" is not an integer."
)
DEPENDENCY_EXISTS: Final[str] = "Issue #{number} is already blocked by #{blocker}"
UNSUPPORTED_QUERY: Final[str] = (
    "the local board answers only the blocked-by query Weekend Loop sends"
)
UNREADABLE_INPUT: Final[str] = "the request body is not JSON: {reason}"
