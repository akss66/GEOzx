import { PlusOutlined } from "@ant-design/icons";
import { useEffect, useRef, useState } from "react";

import type { PublicSkill } from "../../types";

type ContextAction = {
  label: string;
  callback?: () => void;
};

export type CapabilityLauncherContextCallbacks = {
  onAddFilesAndMaterials?: () => void;
  onAddAccountDataPackage?: () => void;
  onAddHistoricalArtifacts?: () => void;
  onSelectAccount?: () => void;
};

export type CapabilityLauncherProps = CapabilityLauncherContextCallbacks & {
  skills?: PublicSkill[];
  onSelectSkill?: (skillCode: string) => void;
  disabled?: boolean;
};

const groupLabels = {
  quick_operations: "快捷运营",
  context: "添加上下文",
  expert_help: "专家协助",
} as const;

export function CapabilityLauncher({
  skills = [],
  onSelectSkill,
  onAddFilesAndMaterials,
  onAddAccountDataPackage,
  onAddHistoricalArtifacts,
  onSelectAccount,
  disabled = false,
}: CapabilityLauncherProps) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const quickOperations = orderedSkills(skills, "quick_operations");
  const contextSkills = orderedSkills(skills, "context");
  const expertHelp = orderedSkills(skills, "expert_help");
  const contextActions: ContextAction[] = [
    { label: "添加文件或素材", callback: onAddFilesAndMaterials },
    { label: "添加账号数据包", callback: onAddAccountDataPackage },
    { label: "添加历史产物", callback: onAddHistoricalArtifacts },
    { label: "选择账号", callback: onSelectAccount },
  ];

  useEffect(() => {
    if (!open) return;

    const firstItem = menuRef.current?.querySelector<HTMLButtonElement>("button:not(:disabled)");
    firstItem?.focus();

    const closeOnOutsidePointer = (event: MouseEvent) => {
      const target = event.target as Node;
      if (menuRef.current?.contains(target) || triggerRef.current?.contains(target)) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", closeOnOutsidePointer);
    return () => document.removeEventListener("mousedown", closeOnOutsidePointer);
  }, [open]);

  useEffect(() => {
    if (disabled) setOpen(false);
  }, [disabled]);

  const closeMenu = (returnFocus = false) => {
    setOpen(false);
    if (returnFocus) triggerRef.current?.focus();
  };

  const moveFocus = (current: HTMLButtonElement, direction: 1 | -1) => {
    const items = Array.from(
      menuRef.current?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)") ?? [],
    );
    const currentIndex = items.indexOf(current);
    if (currentIndex < 0 || items.length === 0) return;
    items[(currentIndex + direction + items.length) % items.length]?.focus();
  };

  const handleMenuKeyDown = (
    event: React.KeyboardEvent<HTMLButtonElement>,
    onActivate: () => void,
  ) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      moveFocus(event.currentTarget, event.key === "ArrowDown" ? 1 : -1);
    }
    if (event.key === "Escape") {
      event.preventDefault();
      closeMenu(true);
    }
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onActivate();
    }
  };

  return (
    <div className="dy-brain-capability-launcher">
      <button
        ref={triggerRef}
        type="button"
        className="dy-brain-capability-trigger"
        aria-label="添加能力或材料"
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen((current) => !current)}
        onKeyDown={(event) => {
          if (event.key !== "Enter" && event.key !== " ") return;
          event.preventDefault();
          setOpen(true);
        }}
      >
        <PlusOutlined aria-hidden="true" />
      </button>

      {open ? (
        <div ref={menuRef} className="dy-brain-capability-menu" role="menu" aria-label="能力与材料">
          <CapabilityGroup label={groupLabels.quick_operations}>
            {quickOperations.map((skill) => (
              <SkillMenuItem
                key={skill.code}
                skill={skill}
                onSelect={() => {
                  onSelectSkill?.(skill.code);
                  closeMenu();
                }}
                onKeyDown={handleMenuKeyDown}
                unavailableReason={skillUnavailableReason(skill, onSelectSkill)}
              />
            ))}
          </CapabilityGroup>
          <CapabilityGroup label={groupLabels.context}>
            {contextSkills.map((skill) => (
              <SkillMenuItem
                key={skill.code}
                skill={skill}
                onSelect={() => {
                  onSelectSkill?.(skill.code);
                  closeMenu();
                }}
                onKeyDown={handleMenuKeyDown}
                unavailableReason={skillUnavailableReason(skill, onSelectSkill)}
              />
            ))}
            {contextActions.map((action) => (
              <ContextMenuItem
                key={action.label}
                action={action}
                onKeyDown={handleMenuKeyDown}
                onSelect={() => {
                  action.callback?.();
                  closeMenu();
                }}
              />
            ))}
          </CapabilityGroup>
          <CapabilityGroup label={groupLabels.expert_help}>
            {expertHelp.map((skill) => (
              <SkillMenuItem
                key={skill.code}
                skill={skill}
                onSelect={() => {
                  onSelectSkill?.(skill.code);
                  closeMenu();
                }}
                onKeyDown={handleMenuKeyDown}
                unavailableReason={skillUnavailableReason(skill, onSelectSkill)}
              />
            ))}
          </CapabilityGroup>
        </div>
      ) : null}
    </div>
  );
}

function CapabilityGroup({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <section className="dy-brain-capability-group" aria-label={label}>
      <h3>{label}</h3>
      <div className="dy-brain-capability-items">{children}</div>
    </section>
  );
}

function SkillMenuItem({
  skill,
  onSelect,
  onKeyDown,
  unavailableReason,
}: {
  skill: PublicSkill;
  onSelect: () => void;
  onKeyDown: (event: React.KeyboardEvent<HTMLButtonElement>, onActivate: () => void) => void;
  unavailableReason: string | null;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      className="dy-brain-capability-item"
      disabled={unavailableReason != null}
      onClick={onSelect}
      onKeyDown={(event) => onKeyDown(event, onSelect)}
    >
      <span>{skill.name}</span>
      <small>{unavailableReason ?? skill.description}</small>
    </button>
  );
}

function ContextMenuItem({
  action,
  onSelect,
  onKeyDown,
}: {
  action: ContextAction;
  onSelect: () => void;
  onKeyDown: (event: React.KeyboardEvent<HTMLButtonElement>, onActivate: () => void) => void;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      className="dy-brain-capability-item"
      disabled={!action.callback}
      onKeyDown={(event) => onKeyDown(event, onSelect)}
      onClick={onSelect}
    >
      {action.label}
    </button>
  );
}

function skillUnavailableReason(
  skill: PublicSkill,
  onSelectSkill: CapabilityLauncherProps["onSelectSkill"],
) {
  if (!skill.is_available) return skill.unavailable_reason || "暂不可用";
  return onSelectSkill ? null : "能力尚未接入";
}

function orderedSkills(
  skills: PublicSkill[],
  category: PublicSkill["category"],
) {
  return skills
    .filter((skill) => skill.category === category)
    .sort((left, right) => {
      if (left.code === "account_inspection") return -1;
      if (right.code === "account_inspection") return 1;
      return left.name.localeCompare(right.name, "zh-CN");
    });
}
