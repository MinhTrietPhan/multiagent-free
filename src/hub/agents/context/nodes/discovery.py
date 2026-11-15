from ..state import State
from ..tools.files import discover


def discovery_node(state: State) -> dict:
    files, manifests = discover(state["root"], state.get("exclude", []))
    return {"files": files, "manifests": manifests}
