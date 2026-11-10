// The remote collector reports fixed English codes; show them in the UI's language.
const MESSAGES={
  'Boot identity unavailable':'无法读取服务器启动标识，区间速率可能不准确',
  'Remote monotonic time unavailable':'无法读取服务器单调时钟，区间速率可能不准确',
  'CPU load unavailable':'无法读取 CPU 负载',
  'CPU counters unavailable':'无法读取 CPU 计数',
  'Memory unavailable':'无法读取内存信息',
  'Network interface identity unavailable':'无法识别部分网卡',
  'Network counter unavailable':'部分网卡流量计数不可用',
  'Network counters unavailable':'无法读取网卡流量计数',
  'Disk usage unavailable':'无法读取磁盘挂载信息',
  'Process identity unavailable':'部分进程无法读取启动身份',
  'Process discovery unavailable':'无法列出进程',
  'GPU unavailable':'未检测到 NVIDIA GPU（nvidia-smi 不可用）',
}
export function warningText(warning){return MESSAGES[warning]||warning}
export function warningTexts(warnings){return (warnings||[]).map(warningText)}
