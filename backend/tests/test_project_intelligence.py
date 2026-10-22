from app import decisions, models, needs, project_intelligence, project_model
from app.ai_analysis import AIContext
from tests.test_ai_analysis import FakeClient, _completed_envelope


def _answer():
    return {
        "summary": "Локальный радар технологий, который должен находить решения под текущие задачи проекта.",
        "technical_model": {
            "summary": "Локальный technology intelligence сервис с длительным collection pipeline.",
            "goals": [{"key": "reliable-discovery", "title": "Надёжно находить технологии", "priority": 3,
                       "outcome": "Сбор и анализ переживают ошибки и дают воспроизводимые результаты."}],
            "subsystems": [{"key": "collection", "title": "Сбор и анализ", "purpose": "Получать и обрабатывать сигналы",
                            "status": "partial", "current_capabilities": ["HTTP collectors", "SQLite persistence"],
                            "stack_evidence": ["FastAPI", "SQLite"]}],
            "gaps": [{"key": "durable-work", "title": "Нет гарантированного восстановления долгих задач",
                      "subsystem_key": "collection", "goal_keys": ["reliable-discovery"], "priority": 3,
                      "why": "Сбор и анализ выполняются долго.", "current_state": "Стадия может прерваться",
                      "desired_state": "Безопасное продолжение и идемпотентный retry"}],
            "risks": [{"title": "Повторная обработка", "why": "После сбоя можно выполнить работу повторно."}],
        },
        "needs": [
            {
                "title": "Надёжные фоновые задачи и восстановление после сбоев",
                "priority": 3,
                "rationale": "Сбор и анализ выполняются долго и должны продолжаться после ошибок.",
                "constraints": "Локальный single-user deployment; не тащить тяжёлую инфраструктуру без необходимости.",
                "acceptance_criteria": "Повторяемые задачи переживают restart и не дублируют результат.",
                "search_terms": ["durable jobs", "task leases", "idempotent workers"],
                "depends_on": ["Ручная потребность"],
                "gap_keys": ["durable-work"],
            },
            {
                "title": "Ручная потребность",
                "priority": 3,
                "rationale": "AI заметил её тоже.",
                "constraints": "AI must not overwrite this",
                "acceptance_criteria": "AI must not overwrite this",
                "search_terms": ["manual need"],
                "depends_on": [],
                "gap_keys": [],
            },
        ],
    }


def test_project_description_infers_structured_needs_and_preserves_manual_ones(db_session):
    db = db_session
    project = models.Project(name="Radar", slug="radar", description=(
        "Personal technology radar that collects sources, ranks discoveries for active projects, "
        "and must recover safely when long collection or analysis work fails."),
        keywords=["technology radar"], stack=["FastAPI", "SQLite"])
    manual = models.Need(title="Ручная потребность", origin="user")
    old_ai = models.Need(title="Старое предположение", origin=project_intelligence.ORIGIN)
    db.add_all([project, manual, old_ai])
    db.flush()
    manual_link = models.ContextNeed(project_id=project.id, need_id=manual.id, priority=1,
                                     constraints="user constraint", search_terms=["manual"])
    old_link = models.ContextNeed(project_id=project.id, need_id=old_ai.id, priority=2,
                                  search_terms=["obsolete"])
    db.add_all([manual_link, old_link])
    db.commit()

    client = FakeClient([_completed_envelope(_answer())])
    proposal = project_intelligence.infer(db, project, context=AIContext(client=client, max_calls=1))
    result = project_intelligence.apply(db, project, proposal)
    db.commit()

    generated = next(link for link in needs.links(db, project.id)
                     if link.need.title.startswith("Надёжные фоновые"))
    assert generated.need.origin == project_intelligence.ORIGIN
    assert generated.priority == 3
    assert generated.search_terms == ["durable jobs", "task leases", "idempotent workers"]
    assert "restart" in generated.acceptance_criteria
    assert manual_link.constraints == "user constraint" and manual_link.priority == 1
    assert old_link.state == "deferred"
    assert result["created"] == 1 and result["preserved"] == 1 and result["deferred"] == 1
    assert generated.need.title in project.needs and "Старое предположение" not in project.needs
    infer_event = (db.query(models.NeedEvent).filter_by(context_need_id=generated.id, action="infer")
                   .order_by(models.NeedEvent.id.desc()).first())
    assert "долго" in infer_event.detail["rationale"]
    row = next(r for r in needs.overview(db, project.id) if r["id"] == generated.id)
    assert row["origin"] == project_intelligence.ORIGIN and "долго" in row["inference_rationale"]
    assert row["readiness"] == "blocked"
    assert [dep["title"] for dep in row["depends_on"]] == ["Ручная потребность"]
    assert result["dependency_count"] == 1 and result["dependencies_created"] == 1
    technical = project_model.serialise(db, project)
    assert technical and technical["stale"] is False
    assert technical["goals"][0]["key"] == "reliable-discovery"
    assert technical["subsystems"][0]["status"] == "partial"
    assert technical["gaps"][0]["needs"][0]["id"] == generated.id
    assert technical["gaps"][0]["needs"][0]["title"] == generated.need.title


def test_high_priority_project_need_gently_reranks_already_relevant_items(db_session):
    db = db_session
    project = models.Project(name="Radar", slug="radar", description="x" * 60)
    high = models.Need(title="Durable jobs", origin=project_intelligence.ORIGIN)
    low = models.Need(title="Analytics", origin=project_intelligence.ORIGIN)
    db.add_all([project, high, low])
    db.flush()
    db.add_all([
        models.ContextNeed(project_id=project.id, need_id=high.id, priority=3, search_terms=["durable jobs"]),
        models.ContextNeed(project_id=project.id, need_id=low.id, priority=1, search_terms=["analytics"]),
    ])
    a = models.Item(name="A", source="github", external_id="o/a", url="u:a",
                    description="durable jobs with retries and leases", momentum_score=0.0)
    b = models.Item(name="B", source="github", external_id="o/b", url="u:b",
                    description="analytics dashboard", momentum_score=0.0)
    db.add_all([a, b])
    db.flush()
    db.add_all([
        models.ItemProjectRelevance(item_id=a.id, project_id=project.id, score=0.70, rationale="relevant"),
        models.ItemProjectRelevance(item_id=b.id, project_id=project.id, score=0.72, rationale="relevant"),
    ])
    db.commit()

    context = needs.item_need_context(db, project, [a, b])
    assert context[a.id]["priority"] == 3 and context[b.id]["priority"] == 1
    # The need signal is bounded, but a central project blocker can beat a tiny raw-relevance difference.
    assert [row.item_id for row in decisions.undecided_for(db, project)][:2] == [a.id, b.id]


def test_project_inference_endpoint_applies_profile_and_refreshes_auto_queries(db_session, monkeypatch):
    from app import config
    from app.routers import projects as projects_router

    db = db_session
    project = models.Project(name="Radar", slug="radar", description=(
        "A personal technology radar that discovers tools for concrete project gaps and ranks them by need."),
        keywords=["technology radar"], stack=["FastAPI"], discovery_enabled=True, lifecycle_status="active")
    db.add(project)
    db.commit()
    monkeypatch.setattr(config, "LLM_ENABLED", True)
    monkeypatch.setattr(project_intelligence, "infer", lambda db, project, context: {
        "summary": "Understood",
        "needs": [{"title": "Hybrid retrieval evaluation", "priority": 3, "rationale": "Core ranking quality",
                   "constraints": "", "acceptance_criteria": "Measure retrieval quality",
                   "search_terms": ["hybrid retrieval", "retrieval evaluation"], "depends_on": []}],
        "provenance": {"provider": "fake"},
    })

    result = projects_router.infer_project_needs(project.id, db)

    assert result["created"] == 1 and result["needs"][0]["priority"] == 3
    assert "Hybrid retrieval evaluation" in project.needs
    assert db.query(models.SearchQuery).filter_by(project_slug="radar", origin="auto").count() >= 1


def test_need_graph_blocks_future_layers_and_unlocks_them_after_prerequisite(db_session):
    db = db_session
    project = models.Project(name="Motion", slug="motion", description="x" * 80)
    foundation = models.Need(title="Pose estimation", origin=project_intelligence.ORIGIN)
    later = models.Need(title="Retargeting", origin=project_intelligence.ORIGIN)
    db.add_all([project, foundation, later])
    db.flush()
    first = models.ContextNeed(project_id=project.id, need_id=foundation.id, priority=3, search_terms=["pose estimation"])
    second = models.ContextNeed(project_id=project.id, need_id=later.id, priority=3, search_terms=["retargeting"])
    db.add_all([first, second])
    db.flush()
    db.add(models.NeedDependency(project_id=project.id, prerequisite_id=first.id, dependent_id=second.id,
                                 origin=project_intelligence.ORIGIN))
    a = models.Item(name="Pose", source="github", external_id="pose", url="u:pose", description="pose estimation")
    b = models.Item(name="Retarget", source="github", external_id="retarget", url="u:retarget", description="retargeting")
    db.add_all([a, b])
    db.commit()

    state = needs.graph_state(db, project.id)
    assert state[first.id]["readiness"] == "ready"
    assert state[second.id]["readiness"] == "blocked"
    assert state[first.id]["unlocks"][0]["id"] == second.id
    context = needs.item_need_context(db, project, [a, b])
    assert context[a.id]["score"] > context[b.id]["score"]
    assert context[b.id]["blocked_by"][0]["id"] == first.id
    assert all(link.id != second.id for link, _, _ in needs.shortlist(db, [first, second]))

    first.state = "satisfied"
    first.result_note = "validated"
    first.result_fingerprint = needs.result_fingerprint(first)
    db.commit()
    state = needs.graph_state(db, project.id)
    assert state[second.id]["readiness"] == "ready"
    assert state[second.id]["blocked_by"] == []
    first.constraints = "Changed device requirements"
    db.commit()
    state = needs.graph_state(db, project.id)
    assert state[first.id]["readiness"] == "stale_result"
    assert state[second.id]["readiness"] == "blocked"
    assert all(link.id != second.id for link, _, _ in needs.shortlist(db, [first, second]))


def test_project_inference_rejects_dependency_cycles(db_session):
    db = db_session
    project = models.Project(name="Graph", slug="graph", description="x" * 80)
    db.add(project)
    db.commit()
    proposal = {"summary": "graph", "needs": [
        {"title": "A", "priority": 3, "rationale": "a", "constraints": "",
         "acceptance_criteria": "", "search_terms": ["aaa"], "depends_on": ["B"]},
        {"title": "B", "priority": 2, "rationale": "b", "constraints": "",
         "acceptance_criteria": "", "search_terms": ["bbb"], "depends_on": ["A"]},
    ]}
    result = project_intelligence.apply(db, project, proposal)
    db.commit()
    edges = needs.dependency_edges(db, project.id)
    assert result["dependency_count"] == 1
    assert len(edges) == 1
    state = needs.graph_state(db, project.id)
    assert sum(meta["readiness"] == "blocked" for meta in state.values()) == 1


def test_technical_model_becomes_stale_when_project_profile_changes(db_session):
    db = db_session
    project = models.Project(name="Model", slug="model", description="A project profile long enough to model reliably.",
                             keywords=["retrieval"], stack=["SQLite"])
    db.add(project)
    db.commit()
    project_model.save(db, project, {
        "summary": "Stored understanding",
        "goals": [{"key": "quality", "title": "Improve quality", "priority": 3, "outcome": "Better results"}],
        "subsystems": [], "gaps": [], "risks": [],
    }, need_rows=[], provenance={"provider": "fake"}, prompt_version="test")
    db.commit()
    assert project_model.serialise(db, project)["stale"] is False

    project.description += " The scope has changed materially."
    db.commit()
    current = project_model.serialise(db, project)
    assert current["stale"] is True
    assert current["source_fingerprint"] != current["current_fingerprint"]


def test_item_need_context_explains_which_project_gap_candidate_closes(db_session):
    db = db_session
    project = models.Project(name="Explain", slug="explain", description="x" * 80)
    need = models.Need(title="Durable jobs", origin=project_intelligence.ORIGIN)
    db.add_all([project, need])
    db.flush()
    link = models.ContextNeed(project_id=project.id, need_id=need.id, priority=3,
                              search_terms=["durable jobs"])
    db.add(link)
    item = models.Item(name="Queue", source="github", external_id="queue", url="u:queue",
                       description="durable jobs with retries")
    db.add(item)
    db.flush()
    project_model.save(db, project, {
        "summary": "Worker system",
        "goals": [{"key": "reliable", "title": "Reliable processing", "priority": 3, "outcome": "Recover safely"}],
        "subsystems": [{"key": "pipeline", "title": "Pipeline", "purpose": "Long work", "status": "partial",
                        "current_capabilities": ["collection"], "stack_evidence": ["SQLite"]}],
        "gaps": [{"key": "durability", "title": "No durable execution", "subsystem_key": "pipeline",
                  "goal_keys": ["reliable"], "priority": 3, "why": "Restarts can interrupt work",
                  "current_state": "best effort", "desired_state": "resume safely"}], "risks": [],
    }, need_rows=[{"id": link.id, "title": need.title, "gap_keys": ["durability"]}], provenance={}, prompt_version="test")
    db.commit()

    meta = needs.item_need_context(db, project, [item])[item.id]
    assert meta["gap"]["key"] == "durability"
    assert meta["gap"]["subsystem"]["title"] == "Pipeline"
    assert meta["gap"]["goals"][0]["title"] == "Reliable processing"
