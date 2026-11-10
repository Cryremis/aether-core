export const workbenchCommands = [
  { name: "help", label: "查看工作台指令" },
  { name: "new", label: "新建会话" },
  { name: "stop", label: "停止当前任务" },
  { name: "context", label: "查看上下文用量与预算" },
  { name: "compact", label: "压缩当前会话上下文" },
  { name: "tools", label: "查看当前会话可用工具" },
  { name: "refresh", label: "向宿主刷新当前会话的工具目录" },
  { name: "deliveries", label: "查看和管理本会话的跨会话投递" },
] as const;

export type WorkbenchCommand = typeof workbenchCommands[number]["name"];
export type CommandAvailability = Partial<Record<WorkbenchCommand, string>>;

export function parseWorkbenchCommand(input: string) {
  const match = /^\/([a-z]+)(?=\s|$)/i.exec(input.trim());
  if (!match) return null;
  const command = workbenchCommands.find(({ name }) => name === match[1].toLowerCase());
  return command ? { name: command.name, hasArguments: input.trim().length > match[0].length } : null;
}
