import axios from 'axios';

const API_URL = process.env.LINGSHU_API_URL || 'http://localhost:8000';

async function submitTask(taskId: string, description: string) {
  try {
    const resp = await axios.post(`${API_URL}/submit_task`, {
      task_id: taskId,
      description: description,
    });
    console.log('Task result:', JSON.stringify(resp.data, null, 2));
    return resp.data;
  } catch (error) {
    console.error('Failed to submit task:', error.message);
    process.exit(1);
  }
}

async function getTaskStatus(taskId: string) {
  try {
    const resp = await axios.get(`${API_URL}/task_status/${taskId}`);
    console.log('Task status:', JSON.stringify(resp.data, null, 2));
    return resp.data;
  } catch (error) {
    console.error('Failed to get task status:', error.message);
  }
}

async function listTasks() {
  try {
    const resp = await axios.get(`${API_URL}/list_tasks`);
    console.log('Tasks:', JSON.stringify(resp.data, null, 2));
    return resp.data;
  } catch (error) {
    console.error('Failed to list tasks:', error.message);
  }
}

// CLI
const [,, cmd, ...args] = process.argv;

switch (cmd) {
  case 'run':
    await submitTask(args[0] || 'TASK_' + Date.now(), args.slice(1).join(' ') || 'Default task');
    break;
  case 'status':
    await getTaskStatus(args[0]);
    break;
  case 'list':
    await listTasks();
    break;
  default:
    console.log(`
Usage: node cli.js <command> [args]

Commands:
  run <description>    Submit a new task
  status <task_id>     Get task status
  list                 List all tasks
    `);
}