"""Host-reviewed applicability containment shared by storage and circuit checks."""

from .core import Refused


def fact_scope(record, key):
    return record["facts"][key].get(
        "applicability", {"state": "all", "coverage": record["coverage"]}
    )


def contains_scope(source, target):
    if source["state"] != "all" and source["state"] != target["state"]:
        return False
    for axis, wanted in target["coverage"].items():
        available = source["coverage"][axis]
        if axis == "thermal_context":
            if available != wanted:
                return False
        elif available[0] > wanted[0] or available[1] < wanted[1]:
            return False
    return True


def validate_fact_scopes(record):
    for key, fact in record["facts"].items():
        scope = fact_scope(record, key)
        for axis, limits in scope["coverage"].items():
            if axis != "thermal_context" and limits[0] > limits[1]:
                raise Refused("reversed fact applicability: " + key + "." + axis)
        if fact["basis"] == "derived":
            for dependency in fact["derivation"]["inputs"]:
                if not contains_scope(fact_scope(record, dependency), scope):
                    raise Refused(
                        "derived applicability exceeds input: "
                        + key
                        + " <- "
                        + dependency
                    )
