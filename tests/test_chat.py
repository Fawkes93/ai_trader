from ai_trader.config import Settings
from ai_trader.db.repo import MemoryRepo
from ai_trader.factor.client import StubFactor
from ai_trader.notify.chat import ChatLog, answer_chat, question_text


def test_question_text_keeps_the_words_after_the_command():
    assert question_text("/ai why no trade?") == "why no trade?"
    assert question_text("/ai@GoldBot why no trade?") == "why no trade?"
    assert question_text("/ai") == ""
    assert question_text(None) == ""


def test_later_question_includes_earlier_turns(tmp_path):
    repo = MemoryRepo()
    repo.insert_run(
        {
            "final_decision": "none",
            "ai_raw_output": {"action": "none", "reasoning": "path was mixed"},
        }
    )
    factor = StubFactor()
    store = ChatLog(tmp_path / "chat.json")
    seen: list[list[dict]] = []

    def ask(settings, messages):
        seen.append(messages)
        return "flat" if len(seen) == 1 else "still flat"

    first = answer_chat(
        settings=Settings(x_handles=()),
        repo=repo,
        factor=factor,
        chat_id="1288151831",
        question="why flat?",
        store=store,
        ask=ask,
    )
    second = answer_chat(
        settings=Settings(x_handles=()),
        repo=repo,
        factor=factor,
        chat_id="1288151831",
        question="and now?",
        store=store,
        ask=ask,
    )
    assert first == "flat"
    assert second == "still flat"
    assert "path was mixed" in seen[0][-1]["content"]
    roles = [(item["role"], item["content"]) for item in seen[1]]
    assert roles[0][0] == "system"
    assert roles[1] == ("user", "why flat?")
    assert roles[2] == ("assistant", "flat")
    assert roles[3][0] == "user"
    assert roles[3][1].startswith("and now?\n\nBook:")
    assert store.turns("1288151831") == [
        {"role": "user", "text": "why flat?"},
        {"role": "assistant", "text": "flat"},
        {"role": "user", "text": "and now?"},
        {"role": "assistant", "text": "still flat"},
    ]


def test_failed_answer_is_not_remembered(tmp_path):
    store = ChatLog(tmp_path / "chat.json")

    def ask(settings, messages):
        raise RuntimeError("down")

    text = answer_chat(
        settings=Settings(x_handles=()),
        repo=MemoryRepo(),
        factor=StubFactor(),
        chat_id="1288151831",
        question="hello",
        store=store,
        ask=ask,
    )
    assert text == "The model did not answer."
    assert store.turns("1288151831") == []


def test_history_keeps_the_last_ten_exchanges(tmp_path):
    store = ChatLog(tmp_path / "chat.json")

    def ask(settings, messages):
        return "ok"

    for index in range(11):
        answer_chat(
            settings=Settings(x_handles=()),
            repo=MemoryRepo(),
            factor=StubFactor(),
            chat_id="1",
            question=f"q{index}",
            store=store,
            ask=ask,
        )
    turns = store.turns("1")
    assert len(turns) == 20
    assert turns[0]["text"] == "q1"
    assert turns[-2]["text"] == "q10"
    assert turns[-1]["text"] == "ok"


def test_empty_question_does_not_call_the_model(tmp_path):
    store = ChatLog(tmp_path / "chat.json")
    called = {"n": 0}

    def ask(settings, messages):
        called["n"] += 1
        return "no"

    text = answer_chat(
        settings=Settings(x_handles=()),
        repo=MemoryRepo(),
        factor=StubFactor(),
        chat_id="1",
        question="  ",
        store=store,
        ask=ask,
    )
    assert text == "Send /ai and then your question."
    assert called["n"] == 0
    assert store.turns("1") == []
