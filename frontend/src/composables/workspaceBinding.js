/** Remote identity excludes display metadata so a list refresh preserves browsing state. */
export function serverBinding(server) {
  return JSON.stringify([server?.id, server?.host, server?.port, server?.username])
}

export function taskBinding(task, server) {
  return JSON.stringify([
    task.id, task.server_id, task.root_path, task.log_path, task.pid, task.process_identity,
    serverBinding(server),
  ])
}
