import { OperationalState } from "../ui";

export function MemberActivity() {
  return (
    <section className="tz-member-tab-panel tz-member-activity">
      <OperationalState
        kind="empty"
        title="成员级操作记录暂不可用"
        description="当前后端还没有提供成员级审计查询接口，因此这里不会伪造操作记录或虚构按钮。"
      />
    </section>
  );
}
