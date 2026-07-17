import { CheckOutlined, EditOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Input, Tag } from "antd";
import { useEffect, useMemo, useState } from "react";

import type { BrainDecisionRequest } from "../../types";

export function DecisionRequest({
  decision,
  selecting,
  revising,
  onSelect,
  onRevise,
}: {
  decision: BrainDecisionRequest;
  selecting: boolean;
  revising: boolean;
  onSelect: (choiceId: string) => void;
  onRevise: (comment: string, requestNewOptions: boolean) => void;
}) {
  const recommendedChoice = useMemo(
    () => decision.choices.find((choice) => choice.recommended) ?? decision.choices[0],
    [decision.choices],
  );
  const [choiceId, setChoiceId] = useState(recommendedChoice?.id ?? "");
  const [customOpen, setCustomOpen] = useState(false);
  const [customDirection, setCustomDirection] = useState("");

  useEffect(() => {
    setChoiceId(recommendedChoice?.id ?? "");
    setCustomOpen(false);
    setCustomDirection("");
  }, [decision.id, recommendedChoice?.id]);

  return (
    <article className="tz-brain-decision" aria-label="主 Agent 方案选择">
      <div className="tz-brain-decision-head">
        <span className="tz-brain-decision-mark" aria-hidden="true">?</span>
        <div>
          <strong>{decision.title}</strong>
          <p>{decision.summary}</p>
        </div>
      </div>

      <div className="tz-brain-decision-options" role="radiogroup" aria-label={decision.title}>
        {decision.choices.map((choice) => (
          <label
            key={choice.id}
            className="tz-brain-decision-option"
            data-selected={choiceId === choice.id || undefined}
          >
            <input
              type="radio"
              name={decision.id}
              value={choice.id}
              checked={choiceId === choice.id}
              onChange={() => setChoiceId(choice.id)}
              aria-label={`${choice.title}：${choice.description}`}
            />
            <span className="tz-brain-decision-radio" aria-hidden="true">
              {choiceId === choice.id ? <CheckOutlined /> : null}
            </span>
            <span className="tz-brain-decision-copy">
              <span className="tz-brain-decision-title">
                <strong>{choice.title}</strong>
                {choice.recommended ? <Tag color="success">推荐</Tag> : null}
              </span>
              <span>{choice.description}</span>
              <small>优势：{choice.benefit} · 取舍：{choice.tradeoff}</small>
            </span>
          </label>
        ))}
      </div>

      {customOpen ? (
        <div className="tz-brain-decision-custom">
          <Input.TextArea
            aria-label="自定义方向"
            value={customDirection}
            rows={2}
            maxLength={1000}
            autoFocus
            placeholder="写下你希望主 Agent 采用的新方向"
            onChange={(event) => setCustomDirection(event.target.value)}
          />
          <Button
            type="primary"
            loading={revising}
            disabled={!customDirection.trim()}
            onClick={() => onRevise(customDirection.trim(), false)}
          >
            提交方向
          </Button>
        </div>
      ) : null}

      <div className="tz-brain-decision-actions">
        <Button
          aria-label="换一批方案"
          icon={<ReloadOutlined />}
          loading={revising}
          onClick={() => onRevise("请基于当前目标重新生成一组差异更明显的方案", true)}
        >
          换一批方案
        </Button>
        {decision.allow_custom_input ? (
          <Button
            aria-label="自定义方向"
            icon={<EditOutlined />}
            onClick={() => setCustomOpen((open) => !open)}
          >
            自定义方向
          </Button>
        ) : null}
        <Button
          type="primary"
          loading={selecting}
          disabled={!choiceId}
          onClick={() => onSelect(choiceId)}
        >
          按此方案继续
        </Button>
      </div>
    </article>
  );
}
