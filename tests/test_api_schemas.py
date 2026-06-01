from guild_manager_bench.api.schemas import ActionRequest


def test_action_request_keeps_recruit_candidate_id() -> None:
    payload = ActionRequest(type="recruit", candidate_id="turn_1_recruit_1").to_payload()

    assert payload["candidate_id"] == "turn_1_recruit_1"
