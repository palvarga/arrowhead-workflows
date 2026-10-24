Output format (one JSON object):

{
  "interpretation": {
    "name": "<CamelCase intent name>",
    "scope": {"line": <int>, "station": "<station>", "tool": "<tool id>", "variant": "<variant>"},
    "goal": "<one sentence>",
    "constraints": ["<hard constraint>", ...],
    "preference": "<soft preference or empty>",
    "priority": "normal | high | low",
    "horizon": "<temporal applicability>",
    "requested_capabilities": ["<capability the operator explicitly asks for that is not in the service list>"],
    "omit_services": ["<service the operator explicitly asks to leave out>"]
  },
  "recipe": {
    "name": "<kebab-case plan name>",
    "first_action": "<name of the first Action>",
    "actions": [
      {
        "name": "<Action name>",
        "next": "<name of the next Action, or null for the last>",
        "steps": [
          {
            "name": "<Step name, unique in the Recipe>",
            "service": "<service definition from the context>",
            "next": ["<successor Step names in the SAME Action>"],
            "dep": ["<Step names whose results this Step needs as input; they must precede it>"],
            "executor": "<Executor name if the Step must be delegated, else null>",
            "params": {"to": "station | crib"}
          }
        ]
      }
    ]
  }
}

Semantics: Actions run strictly one after another along "next". Inside an Action, a Step starts when all
Steps that list it in "next" have completed; Steps without such a predecessor start first; Steps without
an order between them may run in parallel. "params" is used only for the destination of transport-tool.
If no Recipe can be proposed, set "recipe" to null.
