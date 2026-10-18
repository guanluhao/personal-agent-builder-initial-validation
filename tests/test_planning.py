from personal_agent_builder.planning import ask_questions, finalize_plan


ANALYSIS = {"summary": "读取数据并汇总", "output_hint": "结果文件",
            "recommended_tools": [{"name": "summarize", "description": "汇总数据",
                                   "arguments": [{"name": "input", "type": "path", "required": True}]}]}


def test_clear_intent_needs_no_extra_question():
    questions = ask_questions(ANALYSIS, ["summarize"], "每次提供不同文件，请汇总并解释结果",
                              planner=lambda *_: {"questions": []})
    assert questions == []
    plan = finalize_plan(ANALYSIS, ["summarize"], "每次提供不同文件，请汇总并解释结果",
                         questions, [], planner=lambda *_: {
                             "title": "数据助手", "welcome": "请添加文件", "usage": "每次添加本次文件",
                             "behavior": "缺少资料时询问", "limits": "只做汇总", "upload_label": "添加文件"})
    assert plan["title"] == "数据助手"
