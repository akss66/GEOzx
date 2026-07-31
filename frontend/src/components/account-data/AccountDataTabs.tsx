export type AccountDataView = "overview" | "import" | "history";

const items: Array<{ value: AccountDataView; label: string }> = [
  { value: "overview", label: "数据概览" },
  { value: "import", label: "导入与补录" },
  { value: "history", label: "导入记录" },
];

type AccountDataTabsProps = {
  value: AccountDataView;
  onChange: (value: AccountDataView) => void;
};

export function AccountDataTabs({ value, onChange }: AccountDataTabsProps) {
  return (
    <div className="account-data-tabs" role="tablist" aria-label="账号数据中心视图">
      {items.map((item) => (
        <button
          key={item.value}
          id={`account-data-tab-${item.value}`}
          type="button"
          role="tab"
          aria-selected={value === item.value}
          aria-controls={`account-data-panel-${item.value}`}
          className={value === item.value ? "is-active" : undefined}
          onClick={() => onChange(item.value)}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}
