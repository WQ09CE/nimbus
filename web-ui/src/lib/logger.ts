/**
 * Frontend Logger
 * 
 * Provides structured logging with timestamps for easier correlation with backend logs.
 */

const LOG_LEVELS = {
  DEBUG: 0,
  INFO: 1,
  WARN: 2,
  ERROR: 3,
};

type LogLevel = keyof typeof LOG_LEVELS;

const CURRENT_LEVEL: LogLevel = (process.env.NEXT_PUBLIC_LOG_LEVEL as LogLevel) || 'INFO';

// Remote logging queue
const LOG_QUEUE: any[] = [];
let flushTimeout: NodeJS.Timeout | null = null;

function formatTime() {
  return new Date().toISOString().split('T')[1].slice(0, -1); // HH:mm:ss.SSS
}

function queueLog(level: string, message: string, data?: any) {
  LOG_QUEUE.push({
    level,
    message,
    data,
    timestamp: new Date().toISOString()
  });

  if (!flushTimeout) {
    flushTimeout = setTimeout(flushLogs, 1000); // Batch every 1s
  }
}

async function flushLogs() {
  if (LOG_QUEUE.length === 0) return;

  const batch = [...LOG_QUEUE];
  LOG_QUEUE.length = 0;
  flushTimeout = null;

  try {
    const apiUrl = process.env.NEXT_PUBLIC_API_URL || '';
    await fetch(`${apiUrl}/api/v1/logs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ entries: batch, source: 'web-ui' })
    });
  } catch (e) {
    // Silently fail to avoid loops
    console.error('Failed to send logs:', e);
  }
}

export const logger = {
  debug: (msg: string, ...args: any[]) => {
    if (LOG_LEVELS[CURRENT_LEVEL] <= LOG_LEVELS.DEBUG) {
      console.debug(`%c[${formatTime()}] [DEBUG] ${msg}`, 'color: #9CA3AF', ...args);
      // Only send INFO+ to server to save bandwidth
    }
  },

  info: (msg: string, ...args: any[]) => {
    if (LOG_LEVELS[CURRENT_LEVEL] <= LOG_LEVELS.INFO) {
      console.info(`%c[${formatTime()}] [INFO] ${msg}`, 'color: #3B82F6', ...args);
      queueLog('info', msg, args.length > 0 ? args[0] : undefined);
    }
  },

  warn: (msg: string, ...args: any[]) => {
    if (LOG_LEVELS[CURRENT_LEVEL] <= LOG_LEVELS.WARN) {
      console.warn(`%c[${formatTime()}] [WARN] ${msg}`, 'color: #F59E0B', ...args);
      queueLog('warn', msg, args.length > 0 ? args[0] : undefined);
    }
  },

  error: (msg: string, ...args: any[]) => {
    if (LOG_LEVELS[CURRENT_LEVEL] <= LOG_LEVELS.ERROR) {
      console.error(`%c[${formatTime()}] [ERROR] ${msg}`, 'color: #EF4444', ...args);
      queueLog('error', msg, args.length > 0 ? args[0] : undefined);
    }
  }
};
