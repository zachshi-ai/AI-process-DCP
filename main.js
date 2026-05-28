const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const fs = require('fs');

// 全局变量
let mainWindow;
let backendProcess;

// 获取应用根目录路径
function getAppRoot() {
  // 在开发环境中，__dirname 是项目根目录
  // 在打包后的应用中，__dirname 是 app.asar 文件所在的目录
  if (process.env.NODE_ENV === 'development') {
    return __dirname;
  } else {
    // 检查是否在 app.asar 中
    if (__dirname.includes('app.asar')) {
      // 在打包后的应用中，后端和前端文件被解压到 app.asar.unpacked 目录
      return path.resolve(__dirname, '..', '..', 'app.asar.unpacked');
    } else {
      return __dirname;
    }
  }
}

const APP_ROOT = getAppRoot();
console.log('应用根目录:', APP_ROOT);

// 创建HTML内容
function createHtmlContent() {
  return `
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AI-DCP 桌面应用</title>
  <style>
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
      margin: 0;
      padding: 0;
      background-color: #f5f5f5;
      color: #333;
    }
    .container {
      max-width: 1200px;
      margin: 0 auto;
      padding: 20px;
    }
    h1 {
      color: #2c3e50;
      text-align: center;
      margin-bottom: 40px;
    }
    .status-section {
      background-color: white;
      border-radius: 8px;
      padding: 20px;
      margin-bottom: 20px;
      box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
    }
    .status-section h2 {
      margin-top: 0;
      color: #34495e;
    }
    .status-item {
      display: flex;
      justify-content: space-between;
      padding: 10px 0;
      border-bottom: 1px solid #eee;
    }
    .status-item:last-child {
      border-bottom: none;
    }
    .status-value {
      font-weight: bold;
    }
    .status-value.connected {
      color: #27ae60;
    }
    .status-value.disconnected {
      color: #e74c3c;
    }
    .api-section {
      background-color: white;
      border-radius: 8px;
      padding: 20px;
      box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
    }
    .api-section h2 {
      margin-top: 0;
      color: #34495e;
    }
    .api-endpoint {
      background-color: #f8f9fa;
      padding: 15px;
      border-radius: 4px;
      margin-bottom: 10px;
      font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
      font-size: 14px;
    }
    .api-method {
      display: inline-block;
      padding: 2px 8px;
      border-radius: 4px;
      font-weight: bold;
      margin-right: 10px;
    }
    .api-method.get {
      background-color: #3498db;
      color: white;
    }
    .api-method.post {
      background-color: #27ae60;
      color: white;
    }
    .footer {
      margin-top: 40px;
      text-align: center;
      color: #7f8c8d;
      font-size: 14px;
    }
  </style>
</head>
<body>
  <div class="container">
    <h1>AI-DCP 桌面应用</h1>
    
    <div class="status-section">
      <h2>服务状态</h2>
      <div class="status-item">
        <span>后端服务</span>
        <span class="status-value" id="backend-status">连接中...</span>
      </div>
      <div class="status-item">
        <span>API 基础路径</span>
        <span class="status-value">http://localhost:8000/api</span>
      </div>
    </div>
    
    <div class="api-section">
      <h2>可用 API 端点</h2>
      <div class="api-endpoint">
        <span class="api-method get">GET</span>
        /api/llm/config - 获取 LLM 配置
      </div>
      <div class="api-endpoint">
        <span class="api-method post">POST</span>
        /api/llm/config - 保存 LLM 配置
      </div>
      <div class="api-endpoint">
        <span class="api-method get">GET</span>
        /api/skills - 获取技能模板列表
      </div>
      <div class="api-endpoint">
        <span class="api-method post">POST</span>
        /api/auth/start - 启动浏览器授权
      </div>
      <div class="api-endpoint">
        <span class="api-method post">POST</span>
        /api/task/process - 处理单个任务
      </div>
      <div class="api-endpoint">
        <span class="api-method post">POST</span>
        /api/task/batch - 批量执行任务
      </div>
      <div class="api-endpoint">
        <span class="api-method get">GET</span>
        /api/history/events - 获取历史事件列表
      </div>
      <div class="api-endpoint">
        <span class="api-method post">POST</span>
        /api/drill/start - 开始网页下钻
      </div>
    </div>
    
    <div class="footer">
      <p>AI-DCP 桌面应用 v1.0.0</p>
    </div>
  </div>
  
  <script>
    // 检查后端服务状态
    function checkBackendStatus() {
      fetch('http://localhost:8000/')
        .then(response => {
          if (response.ok) {
            document.getElementById('backend-status').textContent = '运行中';
            document.getElementById('backend-status').className = 'status-value connected';
          } else {
            document.getElementById('backend-status').textContent = '未运行';
            document.getElementById('backend-status').className = 'status-value disconnected';
          }
        })
        .catch(error => {
          document.getElementById('backend-status').textContent = '未运行';
          document.getElementById('backend-status').className = 'status-value disconnected';
        });
    }
    
    // 初始检查
    checkBackendStatus();
    
    // 定期检查
    setInterval(checkBackendStatus, 5000);
  </script>
</body>
</html>
  `;
}

// 启动后端服务
function startBackend() {
  console.log('启动后端服务...');
  
  // 检查是否有Python虚拟环境
  const venvPath = path.join(APP_ROOT, 'backend', 'venv', 'bin', 'python');
  console.log('虚拟环境路径:', venvPath);
  console.log('虚拟环境是否存在:', fs.existsSync(venvPath));
  const pythonPath = fs.existsSync(venvPath) ? venvPath : 'python3';
  console.log('使用的Python路径:', pythonPath);
  
  // 启动后端服务
  const backendMainPath = path.join(APP_ROOT, 'backend', 'main.py');
  console.log('后端主文件路径:', backendMainPath);
  console.log('后端主文件是否存在:', fs.existsSync(backendMainPath));
  
  if (!fs.existsSync(backendMainPath)) {
    console.error('后端主文件不存在:', backendMainPath);
    return;
  }
  
  try {
    backendProcess = spawn(pythonPath, [backendMainPath], {
      env: {
        ...process.env,
        PYTHONUNBUFFERED: '1'
      }
    });
    
    console.log('后端进程已启动，PID:', backendProcess.pid);
    
    // 监听后端输出
    backendProcess.stdout.on('data', (data) => {
      console.log('后端输出:', data.toString());
    });
    
    // 监听后端错误
    backendProcess.stderr.on('data', (data) => {
      console.error('后端错误:', data.toString());
    });
    
    // 监听后端退出
    backendProcess.on('close', (code) => {
      console.log(`后端服务退出，代码: ${code}`);
    });
    
    // 监听后端错误事件
    backendProcess.on('error', (error) => {
      console.error('后端进程错误:', error);
    });
  } catch (error) {
    console.error('启动后端服务时发生错误:', error);
  }
}

// 创建主窗口
function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false
    },
    icon: path.join(__dirname, 'assets', 'icon.icns')
  });
  
  // 检查是否有前端构建产物
  const frontendDistPath = path.join(APP_ROOT, 'frontend', 'dist', 'index.html');
  console.log('前端构建产物路径:', frontendDistPath);
  console.log('前端构建产物是否存在:', fs.existsSync(frontendDistPath));
  
  // 检查APP_ROOT路径
  console.log('APP_ROOT:', APP_ROOT);
  
  // 检查前端目录是否存在
  const frontendDir = path.join(APP_ROOT, 'frontend');
  console.log('前端目录:', frontendDir);
  console.log('前端目录是否存在:', fs.existsSync(frontendDir));
  
  // 检查前端dist目录是否存在
  const frontendDistDir = path.join(APP_ROOT, 'frontend', 'dist');
  console.log('前端dist目录:', frontendDistDir);
  console.log('前端dist目录是否存在:', fs.existsSync(frontendDistDir));
  
  if (fs.existsSync(frontendDistPath)) {
    // 加载前端构建产物
    console.log('加载前端构建产物');
    
    // 尝试使用loadFile加载
    try {
      mainWindow.loadFile(frontendDistPath);
      console.log('使用loadFile加载前端构建产物');
    } catch (error) {
      console.error('loadFile加载失败:', error);
      // 尝试使用loadURL加载
      mainWindow.loadURL(`file://${frontendDistPath}`);
      console.log('使用loadURL加载前端构建产物');
    }
    
    // 注入API基础路径
    mainWindow.webContents.on('did-finish-load', () => {
      console.log('前端页面加载完成，注入API基础路径');
      mainWindow.webContents.executeJavaScript(`
        window.__AI_DCP_API_BASE = 'http://localhost:8000';
        console.log('API基础路径已注入:', window.__AI_DCP_API_BASE);
      `);
    });
    
    // 监听前端页面加载失败
    mainWindow.webContents.on('did-fail-load', (event, errorCode, errorDescription, validatedURL, isMainFrame) => {
      console.error('前端页面加载失败:', errorCode, errorDescription, validatedURL);
      // 如果加载失败，尝试加载内置的HTML内容
      console.log('尝试加载内置的HTML内容');
      const htmlContent = createHtmlContent();
      mainWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(htmlContent)}`);
    });
  } else {
    // 加载内置的HTML内容
    console.log('前端构建产物不存在，加载内置的HTML内容');
    const htmlContent = createHtmlContent();
    mainWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(htmlContent)}`);
  }
  
  // 打开开发者工具
  // mainWindow.webContents.openDevTools();
  
  // 窗口关闭时的处理
  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// 应用准备就绪
app.on('ready', () => {
  // 启动后端服务
  startBackend();
  
  // 创建主窗口
  createWindow();
});

// 所有窗口关闭时的处理
app.on('window-all-closed', () => {
  // 终止后端进程
  if (backendProcess) {
    backendProcess.kill();
  }
  
  // 在macOS上，应用图标会保留在dock中
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

// 激活应用时的处理
app.on('activate', () => {
  if (mainWindow === null) {
    createWindow();
  }
});

// 处理IPC通信
ipcMain.on('message', (event, message) => {
  console.log('收到消息:', message);
  event.reply('reply', '消息已收到');
});

// 处理后端服务控制
ipcMain.on('backend:start', (event) => {
  console.log('收到启动后端服务的请求');
  if (!backendProcess || backendProcess.killed) {
    startBackend();
    event.reply('backend:status', { running: true });
  } else {
    event.reply('backend:status', { running: true });
  }
});

ipcMain.on('backend:stop', (event) => {
  console.log('收到停止后端服务的请求');
  if (backendProcess && !backendProcess.killed) {
    backendProcess.kill();
    event.reply('backend:status', { running: false });
  } else {
    event.reply('backend:status', { running: false });
  }
});

ipcMain.on('backend:status', (event) => {
  console.log('收到查询后端服务状态的请求');
  const running = backendProcess && !backendProcess.killed;
  event.reply('backend:status', { running });
});