"""Size a crypto experiment without calling a model or data vendor."""


def build_plan(assets: int = 100, days: int = 30) -> dict:
    if type(assets) is not int or not 1 <= assets <= 100:
        raise ValueError("assets must be an integer from 1 to 100")
    if type(days) is not int or days < 1:
        raise ValueError("days must be a positive integer")
    return {
        "assets": assets,
        "days": days,
        "provider": "chatgpt",
        "paid_api_fallback": False,
        "cadences": [
            {
                "hours": hours,
                "decisions_per_day": assets * (24 // hours),
                "decisions_total": assets * (24 // hours) * days,
                "graph_supported": hours == 24,
            }
            for hours in (1, 4, 24)
        ],
        "limitations": [
            "Each decision uses multiple model calls. These counts are not token or usage estimates.",
            "ChatGPT plan access has usage limits. No run or scheduler starts from this plan.",
            "The upstream graph supports daily analysis only. Intraday data cutoffs need implementation.",
            "Top-100 membership and tradeable vendor instruments require a dated universe and identity review.",
        ],
    }


def render_plan(plan: dict) -> str:
    lines = [f"Crypto benchmark plan for {plan['assets']} assets over {plan['days']} days.",
             "Model access uses your ChatGPT plan. Paid API fallback is disabled.", ""]
    for cadence in plan["cadences"]:
        support = "daily graph supported" if cadence["graph_supported"] else "not implemented"
        lines.append(
            f"{cadence['hours']:2}h: {cadence['decisions_per_day']:,} decisions/day, "
            f"{cadence['decisions_total']:,} total. {support}."
        )
    return "\n".join([*lines, "", *plan["limitations"]])
