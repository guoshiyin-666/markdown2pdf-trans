import os
import sys
import threading
import uuid
import shutil
import zipfile
import re
import datetime
import requests
import hashlib
import pathlib
import base64
import mimetypes
import json
import traceback
import time
from flask import Flask, request, jsonify, render_template_string, send_from_directory, Response, send_file
from werkzeug.utils import secure_filename

# Lazy import heavy dependencies
try:
    from pypdf import PdfReader
except ImportError:
    print("Warning: pypdf not found. Page counting will be disabled.")
    PdfReader = None

# ==============================================================================
# AI Model & API Configuration
# ==============================================================================
AI_API_URL = ""
AI_API_KEY = ""
AI_MODEL = ""

TRANSLATION_PROMPT = """
You are a professional linguist and expert document translator. Your task is to translate the following Markdown text into {target_language}.

**Critical Instructions:**
1.  **Preserve Formatting:** You MUST preserve all original Markdown formatting perfectly. This includes, but is not limited to:
    * Headings (`#`, `##`, etc.)
    * Lists (ordered, unordered, and nested)
    * Bold (`**text**`) and Italic (`*text*`) styling
    * Blockquotes (`>`)
    * Tables (`| Header | ...`)
    * Image links (`![alt text](url)`)
    * Hyperlinks (`[link text](url)`)
2.  **Code Blocks:** DO NOT translate any content inside code blocks (```...```) or inline code (`...`). Leave the code as it is.
3.  **Accuracy and Tone:** Translate the textual content with high accuracy, maintaining the original tone and context. The translation should be grammatically flawless and natural-sounding in {target_language}.
4.  **Output ONLY Translation:** Your output must ONLY be the translated Markdown text. Do not add any extra explanations, apologies, or comments like "Here is the translation:".
5.  **Respectful Language:** Ensure the translation is professional and respectful, completely free of any insulting or offensive language.

Translate the following Markdown content:
"""

FILENAME_TRANSLATION_PROMPT = """
You are an expert file name translator. Translate the following text to {target_language} to be used as a valid file name.
**Critical Instructions:**
1.  Provide a concise and accurate translation.
2.  Replace spaces with underscores (_).
3.  Do not include any special characters that are invalid for file names (e.g., /\\:*?"<>|).
4.  Output ONLY the translated text. Do not add any explanation.

Translate the following text:
"""


# ==============================================================================
# Global Configuration and State Management
# ==============================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, 'output')
os.makedirs(OUTPUT_DIR, exist_ok=True)

TASKS = {}
TASKS_LOCK = threading.Lock()
TRANSLATION_CACHE = {}
TRANSLATION_CACHE_LOCK = threading.Lock()

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024

# ==============================================================================
# Frontend HTML Template
# ==============================================================================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI 文档翻译服务</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@300;400;500;700&display=swap');
        body { background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%); font-family: 'Noto Sans SC', sans-serif; min-height: 100vh; }
        .container { max-width: 1600px; }
        .card { background-color: rgba(255, 255, 255, 0.9); backdrop-filter: blur(10px); border: none; border-radius: 15px; box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.37); }
        .log-container { background-color: #1e1e1e; color: #d4d4d4; font-family: 'SF Mono', 'Courier New', monospace; max-height: 400px; overflow-y: auto; border-radius: 8px; padding: 1rem; border: 1px solid #333; }
        .log-container pre { white-space: pre-wrap; word-break: break-word; margin: 0; font-size: 0.85rem; line-height: 1.6; }
        .progress { height: 2rem; font-size: 1rem; }
        .btn-action { background: linear-gradient(45deg, #0d6efd, #0dcaf0); border: none; padding: 0.75rem 1.5rem; font-size: 1.1rem; border-radius: 0.5rem; transition: all 0.3s ease; color: white; }
        .btn-action:hover { transform: translateY(-2px); box-shadow: 0 4px 15px rgba(13, 110, 253, 0.4); }
        .btn-action:disabled { background: #6c757d; box-shadow: none; }
        .preview-pane { height: 75vh; border: 1px solid #dee2e6; border-radius: 8px; background-color: #f8f9fa; }
        .form-control-color { width: 100%; height: calc(1.5em + .75rem + 2px); padding: .375rem; }
        .form-label { font-weight: 500; }
        legend { font-weight: 700; }
    </style>
</head>
<body>
    <div class="container my-5">
        <div class="card p-4 p-md-5">
            <div class="card-body">
                <h2 class="card-title text-center mb-5"><i class="bi bi-robot me-2" style="color: #0d6efd;"></i>AI 文档翻译中心 v8 (任务控制)</h2>
                
                <div class="row gx-4">
                    <!-- Left Side: Controls -->
                    <div class="col-lg-4">
                        <form id="uploadForm">
                            <fieldset class="mb-4">
                                <legend class="form-label fs-5 mb-3">1. 上传文件</legend>
                                <div class="alert alert-info" id="status-message">选择 .md 文件所在的ZIP包或文件夹。</div>
                                <div class="form-check form-check-inline">
                                    <input class="form-check-input" type="radio" name="upload_type" id="zipRadio" value="zip" checked>
                                    <label class="form-check-label" for="zipRadio">ZIP压缩包</label>
                                </div>
                                <div class="form-check form-check-inline">
                                    <input class="form-check-input" type="radio" name="upload_type" id="folderRadio" value="folder">
                                    <label class="form-check-label" for="folderRadio">文件夹</label>
                                </div>
                                <div class="form-check form-check-inline">
                                    <input class="form-check-input" type="radio" name="upload_type" id="fileRadio" value="file">
                                    <label class="form-check-label" for="fileRadio">单个文件</label>
                                </div>
                                <div class="mt-3">
                                    <input class="form-control form-control-lg" type="file" id="file_input" name="files">
                                </div>
                            </fieldset>

                            <div id="main-controls" style="display: none;">
                                <fieldset class="mb-3">
                                    <legend class="form-label fs-5 mb-3">2. 控制面板</legend>
                                    <div class="mb-3">
                                        <label for="preview_file_select" class="form-label">选择预览文件</label>
                                        <select id="preview_file_select" class="form-select"></select>
                                    </div>
                                    <div>
                                        <label for="target_language" class="form-label">选择目标语言</label>
                                        <select id="target_language" class="form-select">
                                            <option value="English">英语 (English)</option>
                                            <option value="Chinese">中文 (Chinese)</option>
                                            <option value="French">法语 (French)</option>
                                            <option value="Russian">俄语 (Russian)</option>
                                            <option value="Spanish">西班牙语 (Spanish)</option>
                                            <option value="Arabic">阿拉伯语 (Arabic)</option>
                                        </select>
                                    </div>
                                </fieldset>
                                
                                <fieldset id="style-options-fieldset" class="mb-4">
                                    <legend class="form-label fs-5 mb-3">3. 自定义PDF样式</legend>
                                    <div class="row g-3">
                                        <div class="col-md-6"><label for="page_orientation" class="form-label">页面方向</label><select id="page_orientation" class="form-select"><option value="portrait">纵向</option><option value="landscape">横向</option></select></div>
                                        <div class="col-md-6"><label for="page_margin" class="form-label">页边距</label><select id="page_margin" class="form-select"><option value="2.5cm">标准</option><option value="2cm">中等</option><option value="1.5cm">较窄</option></select></div>
                                        <div class="col-md-6"><label for="font_family" class="form-label">正文字体</label><select id="font_family" class="form-select"><option value='"Times New Roman", "Microsoft YaHei", serif'>宋体/衬线</option><option value='"Helvetica", "Arial", "Microsoft YaHei", sans-serif'>黑体/非衬线</option></select></div>
                                        <div class="col-md-6"><label for="font_size" class="form-label">正文字号</label><select id="font_size" class="form-select"><option value="12pt">12pt</option><option value="11pt">11pt</option><option value="10pt">10pt</option></select></div>
                                        <div class="col-md-6"><label for="line_height" class="form-label">行间距</label><select id="line_height" class="form-select"><option value="1.7">1.7</option><option value="1.5">1.5</option><option value="2.0">2.0</option></select></div>
                                        <div class="col-md-6"><label for="text_align" class="form-label">文本对齐</label><select id="text_align" class="form-select"><option value="justify">两端对齐</option><option value="left">左对齐</option></select></div>
                                        <div class="col-md-6"><label for="heading_weight" class="form-label">标题粗细</label><select id="heading_weight" class="form-select"><option value="700">粗体</option><option value="normal">常规</option></select></div>
                                        <div class="col-md-6"><label for="code_theme" class="form-label">代码高亮</label><select id="code_theme" class="form-select"><option value="kate">Kate</option><option value="pygments">Pygments</option><option value="tango">Tango</option></select></div>
                                        <div class="col-md-6"><label for="code_font_size" class="form-label">代码字号</label><select id="code_font_size" class="form-select"><option value="85%">85%</option><option value="100%">100%</option><option value="75%">75%</option></select></div>
                                        <div class="col-md-6"><label for="text_color" class="form-label">正文颜色</label><input type="color" id="text_color" class="form-control form-control-color" value="#333333"></div>
                                        <div class="col-md-6"><label for="heading_color" class="form-label">标题颜色</label><input type="color" id="heading_color" class="form-control form-control-color" value="#000000"></div>
                                        <div class="col-md-6"><label for="link_color" class="form-label">链接颜色</label><input type="color" id="link_color" class="form-control form-control-color" value="#0d6efd"></div>
                                        <div class="col-md-6"><label for="quote_bg_color" class="form-label">引用块背景</label><input type="color" id="quote_bg_color" class="form-control form-control-color" value="#f9f9f9"></div>
                                        <div class="col-md-6"><label for="quote_border_color" class="form-label">引用块边框</label><input type="color" id="quote_border_color" class="form-control form-control-color" value="#cccccc"></div>
                                    </div>
                                </fieldset>
                            </div>
                        </form>
                    </div>

                    <!-- Right Side: Preview -->
                    <div class="col-lg-8">
                        <div class="row">
                            <div class="col-md-6">
                                <h4 class="text-center mb-3">原文预览</h4>
                                <iframe id="preview-original" class="preview-pane w-100" title="Original PDF Preview"></iframe>
                            </div>
                            <div class="col-md-6">
                                <h4 class="text-center mb-3">译文预览</h4>
                                <iframe id="preview-translated" class="preview-pane w-100" title="Translated PDF Preview"></iframe>
                            </div>
                        </div>
                    </div>
                </div>
                
                <div id="conversion-starter" style="display: none;">
                    <hr class="my-5">
                    <div class="row align-items-center justify-content-center">
                        <div class="col-md-auto">
                            <label for="export_mode" class="form-label fs-5">批量导出模式:</label>
                        </div>
                        <div class="col-md-6">
                             <select id="export_mode" class="form-select form-select-lg">
                                <option value="translated">仅译文 (文件名将翻译)</option>
                                <option value="original">仅原文</option>
                                <option value="bilingual">双语对照 + 单独译文</option>
                            </select>
                        </div>
                        <div class="col-md-3 d-grid">
                             <button type="button" id="convertBtn" class="btn btn-action fw-bold"><i class="bi bi-lightning-charge-fill me-2"></i>开始批量处理</button>
                        </div>
                    </div>
                </div>

                <div id="progress-area" class="mt-4" style="display: none;">
                    <h3 class="text-center mb-4">批量处理进度</h3>
                    <div class="progress" role="progressbar">
                        <div id="progress-bar" class="progress-bar progress-bar-striped progress-bar-animated" style="width: 0%;">0%</div>
                    </div>
                    <div id="task-controls" class="text-center mt-3" style="display: none;">
                        <button id="pauseBtn" class="btn btn-warning"><i class="bi bi-pause-fill me-1"></i>暂停</button>
                        <button id="resumeBtn" class="btn btn-success" style="display:none;"><i class="bi bi-play-fill me-1"></i>继续</button>
                        <button id="stopBtn" class="btn btn-danger ms-2"><i class="bi bi-stop-circle-fill me-1"></i>结束</button>
                    </div>
                    <h4 class="mt-4 mb-3">实时日志</h4>
                    <div id="log-container" class="log-container"></div>
                    <div id="download-area" class="d-grid mt-4" style="display: none;">
                        <a id="download-link" href="#" class="btn btn-success btn-lg"><i class="bi bi-cloud-download me-2"></i>下载结果</a>
                    </div>
                </div>
            </div>
        </div>
    </div>
    <script>
    document.addEventListener('DOMContentLoaded', function() {
        let currentTaskId = null;

        const ui = {
            zipRadio: document.getElementById('zipRadio'),
            folderRadio: document.getElementById('folderRadio'),
            fileRadio: document.getElementById('fileRadio'),
            fileInput: document.getElementById('file_input'),
            mainControls: document.getElementById('main-controls'),
            previewFileSelect: document.getElementById('preview_file_select'),
            targetLanguage: document.getElementById('target_language'),
            statusMessage: document.getElementById('status-message'),
            previewOriginal: document.getElementById('preview-original'),
            previewTranslated: document.getElementById('preview-translated'),
            conversionStarter: document.getElementById('conversion-starter'),
            exportMode: document.getElementById('export_mode'),
            convertBtn: document.getElementById('convertBtn'),
            progressArea: document.getElementById('progress-area'),
            progressBar: document.getElementById('progress-bar'),
            taskControls: document.getElementById('task-controls'),
            pauseBtn: document.getElementById('pauseBtn'),
            resumeBtn: document.getElementById('resumeBtn'),
            stopBtn: document.getElementById('stopBtn'),
            logContainer: document.getElementById('log-container'),
            downloadArea: document.getElementById('download-area'),
            downloadLink: document.getElementById('download-link'),
            styleOptions: document.getElementById('style-options-fieldset'),
        };

        function debounce(func, delay) {
            let timeout;
            return function(...args) {
                const context = this;
                clearTimeout(timeout);
                timeout = setTimeout(() => func.apply(context, args), delay);
            };
        }

        const debouncedPreview = debounce(generateSideBySidePreview, 500);

        const eventListeners = [
            [ui.zipRadio, 'change', toggleUploadMode],
            [ui.folderRadio, 'change', toggleUploadMode],
            [ui.fileRadio, 'change', toggleUploadMode],
            [ui.fileInput, 'change', handleFileSelection],
            [ui.convertBtn, 'click', startConversion],
            [ui.pauseBtn, 'click', () => controlTask('pause')],
            [ui.resumeBtn, 'click', () => controlTask('resume')],
            [ui.stopBtn, 'click', () => controlTask('stop')],
            [ui.previewFileSelect, 'change', debouncedPreview],
            [ui.targetLanguage, 'change', debouncedPreview]
        ];
        eventListeners.forEach(([el, evt, handler]) => el.addEventListener(evt, handler));
        ui.styleOptions.querySelectorAll('select, input').forEach(el => el.addEventListener('change', debouncedPreview));
        
        function getStyleOptions() {
            const options = {};
            ui.styleOptions.querySelectorAll('select, input').forEach(el => options[el.id] = el.value);
            return options;
        }

        function toggleUploadMode() {
            const isFolder = ui.folderRadio.checked;
            const isFile = ui.fileRadio.checked;
            
            ui.fileInput.webkitdirectory = isFolder;
            ui.fileInput.directory = isFolder;
            ui.fileInput.multiple = isFolder;
            
            if (isFile) {
                ui.fileInput.accept = '.md';
            } else if (isFolder) {
                ui.fileInput.accept = '';
            } else { // ZIP
                ui.fileInput.accept = '.zip';
            }
            resetState();
        }
        
        function resetState() {
            currentTaskId = null;
            ui.fileInput.value = '';
            ui.mainControls.style.display = 'none';
            ui.conversionStarter.style.display = 'none';
            ui.statusMessage.textContent = '选择 .md 文件所在的ZIP包或文件夹。';
            ui.statusMessage.className = 'alert alert-info';
            
            ui.previewOriginal.src = 'about:blank';
            ui.previewTranslated.src = 'about:blank';
            
            ui.progressArea.style.display = 'none';
            ui.taskControls.style.display = 'none';
        }

        async function handleFileSelection(event) {
            const formData = new FormData();
            formData.append('upload_type', document.querySelector('input[name="upload_type"]:checked').value);
            const files = ui.fileInput.files;
            if (!files || files.length === 0) return;

            if (ui.zipRadio.checked) {
                formData.append('zipfile', files[0]);
            } else if (ui.folderRadio.checked) {
                for (const file of files) { formData.append('files[]', file, file.webkitRelativePath); }
            } else { // Single file
                formData.append('file', files[0]);
            }

            ui.statusMessage.textContent = '文件上传和预处理中...';
            ui.statusMessage.className = 'alert alert-warning';
            ui.conversionStarter.style.display = 'none';

            try {
                const response = await fetch('/prepare_upload', { method: 'POST', body: formData });
                const data = await response.json();
                if (!response.ok) throw new Error(data.error || '服务器准备文件失败');

                currentTaskId = data.task_id;
                
                if (data.preview_files && data.preview_files.length > 0) {
                    ui.statusMessage.textContent = `✅ 准备就绪！在您上传的内容中，共找到 ${data.preview_files.length} 个有效的Markdown文件。`;
                    ui.statusMessage.className = 'alert alert-success';
                    
                    ui.previewFileSelect.innerHTML = '';
                    data.preview_files.forEach(file => {
                        const option = document.createElement('option');
                        option.value = file;
                        option.textContent = file;
                        option.title = file;
                        ui.previewFileSelect.appendChild(option);
                    });
                    
                    ui.mainControls.style.display = 'block';
                    ui.conversionStarter.style.display = 'block';
                    generateSideBySidePreview();
                } else {
                    ui.statusMessage.textContent = `⚠️ 上传成功，但未找到可预览的.md文件。`;
                    ui.statusMessage.className = 'alert alert-warning';
                }
            } catch (error) {
                ui.statusMessage.textContent = `❌ 错误: ${error.message}`;
                ui.statusMessage.className = 'alert alert-danger';
                resetState();
            }
        }

        async function generateSideBySidePreview() {
            if (!currentTaskId || !ui.previewFileSelect.value) return;

            const payload = {
                task_id: currentTaskId,
                style_options: getStyleOptions(),
                preview_file: ui.previewFileSelect.value,
                target_language: ui.targetLanguage.value
            };
            
            const loadingHtml = (message) => `<!DOCTYPE html><html lang="en"><body style="font-family: sans-serif; text-align: center; padding: 2rem; color: #6c757d;"><h3>${message}</h3></body></html>`;
            const errorHtml = (message) => `<!DOCTYPE html><html lang="en"><body style="font-family: sans-serif; text-align: center; padding: 2rem; color: #dc3545;"><h3>${message}</h3></body></html>`;

            // Clear previous previews and show a loading state
            ui.previewOriginal.src = 'data:text/html;charset=utf-8,' + encodeURIComponent(loadingHtml('加载中...'));
            ui.previewTranslated.src = 'data:text/html;charset=utf-8,' + encodeURIComponent(loadingHtml('翻译中...'));

            // Generate Original Preview
            fetch('/preview/original', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            }).then(async res => {
                if (!res.ok) throw new Error('原文预览生成失败');
                return res.blob();
            }).then(blob => {
                ui.previewOriginal.src = URL.createObjectURL(blob);
            }).catch(err => {
                console.error("Original preview error:", err);
                ui.previewOriginal.src = 'data:text/html;charset=utf-8,' + encodeURIComponent(errorHtml(err.message));
            });

            // Generate Translated Preview
            fetch('/preview/translated', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            }).then(async res => {
                if (!res.ok) throw new Error('译文预览生成失败');
                return res.blob();
            }).then(blob => {
                ui.previewTranslated.src = URL.createObjectURL(blob);
            }).catch(err => {
                console.error("Translated preview error:", err);
                ui.previewTranslated.src = 'data:text/html;charset=utf-8,' + encodeURIComponent(errorHtml(err.message));
            });
        }
        
        function startConversion() {
            if (!currentTaskId) return;
            ui.convertBtn.disabled = true;
            ui.convertBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> 处理中...';
            ui.progressArea.style.display = 'block';
            ui.taskControls.style.display = 'block';
            ui.logContainer.innerHTML = '';
            ui.downloadArea.style.display = 'none';
            ui.progressBar.style.width = '0%';
            ui.progressBar.textContent = '0%';
            ui.progressBar.classList.remove('bg-danger', 'bg-success');
            
            fetch('/start_conversion', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    task_id: currentTaskId, 
                    style_options: getStyleOptions(),
                    target_language: ui.targetLanguage.value,
                    export_mode: ui.exportMode.value
                })
            })
            .then(res => res.json())
            .then(data => {
                if (data.error) throw new Error(data.error);
                pollStatus(data.task_id);
            })
            .catch(error => {
                alert(`开始任务失败: ${error.message}`);
                ui.convertBtn.disabled = false;
                ui.convertBtn.innerHTML = '<i class="bi bi-lightning-charge-fill me-2"></i>开始批量处理';
                ui.taskControls.style.display = 'none';
            });
        }
        
        function controlTask(action) {
            if (!currentTaskId) return;
            fetch(`/${action}/${currentTaskId}`, { method: 'POST' })
                .then(res => {
                    if (!res.ok) {
                        console.error(`Failed to ${action} task`);
                    }
                });
        }

        function pollStatus(taskId) {
            const interval = setInterval(() => {
                fetch(`/status/${taskId}`)
                .then(res => res.json())
                .then(statusData => {
                    ui.progressBar.style.width = statusData.progress + '%';
                    ui.progressBar.textContent = statusData.progress + '%';
                    if (statusData.logs && statusData.logs.length > 0) {
                         statusData.logs.forEach(logEntry => appendLog(logEntry));
                    }

                    // Update task control buttons based on state
                    if (statusData.state === 'RUNNING') {
                        ui.pauseBtn.style.display = 'inline-block';
                        ui.resumeBtn.style.display = 'none';
                        ui.stopBtn.disabled = false;
                    } else if (statusData.state === 'PAUSED') {
                        ui.pauseBtn.style.display = 'none';
                        ui.resumeBtn.style.display = 'inline-block';
                        ui.stopBtn.disabled = false;
                    }

                    if (statusData.state === 'SUCCESS' || statusData.state === 'FAILURE' || statusData.state === 'STOPPED') {
                        clearInterval(interval);
                        ui.convertBtn.disabled = false;
                        ui.convertBtn.innerHTML = '<i class="bi bi-lightning-charge-fill me-2"></i>开始批量处理';
                        ui.taskControls.style.display = 'none';
                        if (statusData.state === 'SUCCESS') {
                            ui.progressBar.classList.add('bg-success');
                            ui.downloadLink.href = statusData.result_url;
                            ui.downloadArea.style.display = 'block';
                        } else {
                            ui.progressBar.classList.add('bg-danger');
                        }
                    }
                });
            }, 1500);
        }

        function appendLog(logEntry) {
            const pre = document.createElement('pre');
            pre.textContent = `> ${logEntry.log}`;
            ui.logContainer.appendChild(pre);
            ui.logContainer.scrollTop = ui.logContainer.scrollHeight;
        }

        toggleUploadMode();
    });
    </script>
</body>
</html>
"""

# ==============================================================================
# Backend Core Logic
# ==============================================================================

@app.errorhandler(Exception)
def handle_global_exception(e):
    traceback.print_exc()
    if hasattr(e, 'code'):
        return jsonify(error=f"HTTP Error: {e.name}", message=e.description), e.code
    return jsonify(error="An unhandled internal server error occurred."), 500

@app.route('/favicon.ico')
def favicon():
    # This prevents the 404 error for the icon in the browser tab.
    return send_file(os.path.join(BASE_DIR, 'static', 'favicon.ico'), mimetype='image/vnd.microsoft.icon')

def update_task_status(task_id, state=None, progress=None, log=None, error=None, result_url=None, preview_files=None):
    with TASKS_LOCK:
        if task_id not in TASKS: TASKS[task_id] = {}
        task = TASKS[task_id]
        if state: task['state'] = state
        if progress is not None: task['progress'] = progress
        if log: task.setdefault('logs', []).append({'log': log})
        if error: task.setdefault('logs', []).append({'log': f"❌ 任务失败: {error}"}); task['error'] = error
        if result_url: task['result_url'] = result_url
        if preview_files is not None: task['preview_files'] = preview_files

def get_and_clear_logs(task_id):
    with TASKS_LOCK:
        logs = TASKS.get(task_id, {}).get('logs', [])
        if logs: TASKS[task_id]['logs'] = []
        return logs

def read_file_with_fallback(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8-sig') as f: return f.read()
    except UnicodeDecodeError:
        with open(file_path, 'r', encoding='gbk', errors='ignore') as f: return f.read()

def get_pdf_page_count(pdf_file_path):
    if not PdfReader: return 'N/A'
    try:
        with open(pdf_file_path, 'rb') as f: return len(PdfReader(f).pages)
    except Exception: return 'N/A'

def preprocess_markdown_images(md_content, md_file_dir):
    def replacer(match):
        alt_text, link = match.group(1), match.group(2)
        if link.startswith(('http://', 'https://', 'data:image')): return match.group(0)
        absolute_image_path = os.path.normpath(os.path.join(md_file_dir, link.split('?')[0]))
        if os.path.exists(absolute_image_path):
            mime_type, _ = mimetypes.guess_type(absolute_image_path)
            if not mime_type: mime_type = 'application/octet-stream'
            with open(absolute_image_path, 'rb') as f: img_data = f.read()
            base64_data = base64.b64encode(img_data).decode('utf-8')
            return f'![{alt_text}](data:{mime_type};base64,{base64_data})'
        return match.group(0)
    return re.sub(r'!\[(.*?)\]\((.*?)\)', replacer, md_content)

def get_css_style(style_options):
    defaults = {
        'font_family': '"Times New Roman", "Microsoft YaHei", serif', 'font_size': '12pt', 
        'page_margin': '2.5cm', 'line_height': '1.7', 'text_align': 'justify', 
        'text_color': '#333333', 'heading_color': '#000000', 'link_color': '#0d6efd',
        'page_orientation': 'portrait', 'heading_weight': '700', 'code_font_size': '85%',
        'quote_bg_color': '#f9f9f9', 'quote_border_color': '#cccccc'
    }
    def get_opt(key): return style_options.get(key, defaults[key])
    return f"""
        @page {{ size: A4 {get_opt('page_orientation')}; margin: {get_opt('page_margin')}; }}
        html {{ font-size: {get_opt('font_size')}; }}
        body {{ font-family: {get_opt('font_family')}; line-height: {get_opt('line_height')}; color: {get_opt('text_color')}; text-align: {get_opt('text_align')}; }}
        a {{ color: {get_opt('link_color')}; text-decoration: none; }}
        h1,h2,h3,h4,h5,h6 {{ color: {get_opt('heading_color')}; text-align: left; font-weight: {get_opt('heading_weight')}; }}
        img {{ max-width: 100%; height: auto; }}
        table {{ width: 100%; border-collapse: collapse; margin: 1.5em 0; }}
        th,td {{ border: 1px solid #ccc; padding: .75em; }}
        pre, code, tt {{ font-size: {get_opt('code_font_size')}; }}
        pre {{ white-space: pre-wrap; }}
        blockquote {{ margin: 1.5em 0; padding: .5em 1.5em; background-color: {get_opt('quote_bg_color')}; border-left: 5px solid {get_opt('quote_border_color')}; }}
        .bilingual-table {{ width: 100%; table-layout: fixed; border-collapse: collapse; }}
        .bilingual-table td {{ width: 50%; vertical-align: top; padding: 5px 10px; border: 1px solid #eee; }}
    """

def unzip_with_encoding_fix(zip_path, extract_dir):
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        for member in zip_ref.infolist():
            try: filename_decoded = member.filename.encode('cp437').decode('utf-8')
            except: filename_decoded = member.filename.encode('cp437').decode('gbk', errors='ignore')
            if filename_decoded.startswith('__MACOSX/'): continue
            member.filename = filename_decoded
            target_path = os.path.join(extract_dir, member.filename)
            if not os.path.realpath(target_path).startswith(os.path.realpath(extract_dir)): continue
            if member.is_dir(): os.makedirs(target_path, exist_ok=True)
            else:
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                with zip_ref.open(member, 'r') as source, open(target_path, 'wb') as target:
                    shutil.copyfileobj(source, target)

def translate_text_via_api(task_id, content, target_language, prompt_template, log_id=""):
    cache_key = hashlib.md5((content + target_language + prompt_template).encode('utf-8')).hexdigest()
    with TRANSLATION_CACHE_LOCK:
        if cache_key in TRANSLATION_CACHE:
            return TRANSLATION_CACHE[cache_key]

    is_preview = not threading.current_thread().name.startswith("conversion_thread")
    if not is_preview:
        update_task_status(task_id, log=f"  -> [AI] Calling API for '{log_id}' (Lang: {target_language})...")
    
    headers = {"Authorization": AI_API_KEY, "Content-Type": "application/json"}
    prompt = prompt_template.format(target_language=target_language)
    payload = { "model": AI_MODEL, "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": content}] }
    
    try:
        response = requests.post(AI_API_URL, headers=headers, json=payload, timeout=180)
        response.raise_for_status()
        translated_content = response.json()['choices'][0]['message']['content'].strip()
        with TRANSLATION_CACHE_LOCK:
            TRANSLATION_CACHE[cache_key] = translated_content
        if not is_preview:
            update_task_status(task_id, log=f"  -> [AI] Successfully received translation for '{log_id}'")
        return translated_content
    except requests.exceptions.RequestException as e:
        raise ConnectionError(f"AI service connection failed: {e}")
    except (KeyError, IndexError) as e:
        raise ValueError(f"Could not parse AI service response: {e}")

def sanitize_filename(name):
    """Removes invalid characters and replaces spaces for use as a filename."""
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    name = re.sub(r'\s+', '_', name)
    return name

def run_conversion_thread(task_id, style_options, target_language, export_mode):
    threading.current_thread().name = f"conversion_thread_{task_id}"
    import pandas as pd
    import pypandoc
    import weasyprint

    with TASKS_LOCK: task_dir = TASKS.get(task_id, {}).get('task_dir')
    if not task_dir: return

    try:
        source_dir = os.path.join(task_dir, 'source')
        result_dir = os.path.join(task_dir, 'result')
        files = [os.path.join(dp, f) for dp, dn, fn in os.walk(source_dir) for f in fn if f.lower().endswith('.md') and not f.startswith('._')]
        if not files: raise ValueError("No .md files found.")

        total_files, report_results = len(files), []
        custom_css = weasyprint.CSS(string=get_css_style(style_options))
        pypandoc_args = [f'--highlight-style={style_options.get("code_theme", "kate")}']

        for i, file_path in enumerate(sorted(files)):
            # --- Task Control Check ---
            with TASKS_LOCK:
                task_state = TASKS[task_id].get('state')
                if task_state == 'STOPPING':
                    update_task_status(task_id, state='STOPPED', log='任务已被用户手动结束。')
                    return
                while task_state == 'PAUSED':
                    time.sleep(1)
                    task_state = TASKS[task_id].get('state')
            # --- End Task Control Check ---

            rel_path = os.path.relpath(file_path, source_dir)
            progress = 10 + int((i / total_files) * 80)
            update_task_status(task_id, 'RUNNING', progress=progress, log=f"({i+1}/{total_files}) Processing: {rel_path}")

            md_content = read_file_with_fallback(file_path)
            original_filename_stem = pathlib.Path(file_path).stem
            file_report = {"Original Filename": pathlib.Path(file_path).name, "Translated Filename": "N/A", "Original Pages": "N/A", "Translated Pages": "N/A", "Bilingual Pages": "N/A"}

            translated_md = ""
            translated_filename_stem = original_filename_stem
            
            if export_mode in ['translated', 'bilingual']:
                translated_md = translate_text_via_api(task_id, md_content, target_language, TRANSLATION_PROMPT, log_id=rel_path)
                translated_filename_stem_raw = translate_text_via_api(task_id, original_filename_stem, target_language, FILENAME_TRANSLATION_PROMPT, log_id=f"filename: {original_filename_stem}")
                translated_filename_stem = sanitize_filename(translated_filename_stem_raw)
                file_report["Translated Filename"] = translated_filename_stem + ".pdf"
            
            # --- Generate Original PDF ---
            if export_mode in ['original', 'bilingual']:
                original_pdf_path = os.path.join(result_dir, 'original_pdfs', os.path.splitext(rel_path)[0] + '.pdf')
                os.makedirs(os.path.dirname(original_pdf_path), exist_ok=True)
                html = pypandoc.convert_text(preprocess_markdown_images(md_content, os.path.dirname(file_path)), 'html', format='markdown+latex_macros', extra_args=pypandoc_args)
                weasyprint.HTML(string=f'<html><body>{html}</body></html>').write_pdf(original_pdf_path, stylesheets=[custom_css])
                file_report["Original Pages"] = get_pdf_page_count(original_pdf_path)

            # --- Generate Translated PDF ---
            if export_mode in ['translated', 'bilingual']:
                translated_pdf_path = os.path.join(result_dir, 'translated_pdfs', os.path.dirname(rel_path), translated_filename_stem + '.pdf')
                os.makedirs(os.path.dirname(translated_pdf_path), exist_ok=True)
                html = pypandoc.convert_text(preprocess_markdown_images(translated_md, os.path.dirname(file_path)), 'html', format='markdown+latex_macros', extra_args=pypandoc_args)
                weasyprint.HTML(string=f'<html><body>{html}</body></html>').write_pdf(translated_pdf_path, stylesheets=[custom_css])
                file_report["Translated Pages"] = get_pdf_page_count(translated_pdf_path)

            # --- Generate Bilingual PDF ---
            if export_mode == 'bilingual':
                bilingual_pdf_path = os.path.join(result_dir, 'bilingual_pdfs', os.path.dirname(rel_path), translated_filename_stem + '.pdf')
                os.makedirs(os.path.dirname(bilingual_pdf_path), exist_ok=True)
                
                original_paras = md_content.split('\n\n')
                translated_paras = translated_md.split('\n\n')
                
                bilingual_html_rows = ""
                num_paras = max(len(original_paras), len(translated_paras))
                for para_idx in range(num_paras):
                    original_para = original_paras[para_idx] if para_idx < len(original_paras) else ""
                    translated_para = translated_paras[para_idx] if para_idx < len(translated_paras) else ""

                    if not original_para.strip() and not translated_para.strip(): continue
                    
                    original_html = pypandoc.convert_text(preprocess_markdown_images(original_para, os.path.dirname(file_path)), 'html', format='markdown+latex_macros', extra_args=pypandoc_args)
                    translated_html = pypandoc.convert_text(preprocess_markdown_images(translated_para, os.path.dirname(file_path)), 'html', format='markdown+latex_macros', extra_args=pypandoc_args)
                    bilingual_html_rows += f"<tr><td>{original_html}</td><td>{translated_html}</td></tr>"
                
                full_bilingual_html = f'<html><body><table class="bilingual-table">{bilingual_html_rows}</table></body></html>'
                weasyprint.HTML(string=full_bilingual_html).write_pdf(bilingual_pdf_path, stylesheets=[custom_css])
                file_report["Bilingual Pages"] = get_pdf_page_count(bilingual_pdf_path)

            report_results.append(file_report)

        update_task_status(task_id, 'PROGRESS', progress=95, log="Generating summary report...")
        pd.DataFrame(report_results).to_csv(os.path.join(result_dir, "translation_summary.csv"), index=False, encoding='utf_8_sig')

        update_task_status(task_id, 'PROGRESS', progress=98, log="Compressing results...")
        zip_filename = f"Translated_Results_{task_id[:8]}.zip"
        zip_path = os.path.join(task_dir, zip_filename)
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, _, files in os.walk(result_dir):
                for file in files:
                    zipf.write(os.path.join(root, file), os.path.relpath(os.path.join(root, file), result_dir))
        
        update_task_status(task_id, 'SUCCESS', progress=100, log="🎉 Task complete! Your download is ready.", result_url=f"/download/{task_id}")

    except Exception as e:
        traceback.print_exc()
        update_task_status(task_id, 'FAILURE', error=str(e))

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/prepare_upload', methods=['POST'])
def prepare_upload():
    task_id = str(uuid.uuid4())
    task_dir = os.path.join(OUTPUT_DIR, task_id)
    source_dir = os.path.join(task_dir, 'source')
    os.makedirs(source_dir, exist_ok=True)
    with TASKS_LOCK: TASKS[task_id] = {'task_dir': task_dir, 'state': 'PREPARING'}
    preview_files = []
    
    upload_type = request.form.get('upload_type')
    if upload_type == 'folder':
        files = request.files.getlist("files[]")
        if not files: return jsonify({'error': 'No folder content selected'}), 400
        for file in files:
            safe_relative_path = os.path.join(*(secure_filename(part) for part in (file.filename or "").split(os.sep)))
            if not safe_relative_path: continue
            destination_path = os.path.join(source_dir, safe_relative_path)
            if not os.path.abspath(destination_path).startswith(os.path.abspath(source_dir)): continue
            os.makedirs(os.path.dirname(destination_path), exist_ok=True)
            file.save(destination_path)
    elif upload_type == 'file':
        file = request.files.get('file')
        if not file or not file.filename.lower().endswith('.md'): return jsonify({'error': 'Please upload a single .md file'}), 400
        filename = secure_filename(file.filename)
        destination_path = os.path.join(source_dir, filename)
        file.save(destination_path)
        preview_files.append(filename) # Explicitly add the single file
    else: # zip
        file = request.files.get('zipfile')
        if not file or not file.filename.lower().endswith('.zip'): return jsonify({'error': 'Please upload a ZIP file'}), 400
        zip_path = os.path.join(task_dir, 'source.zip')
        file.save(zip_path)
        unzip_with_encoding_fix(zip_path, source_dir)
    
    # If preview_files is not already populated (i.e., not a single file upload)
    if not preview_files:
        preview_files = [os.path.relpath(os.path.join(dp, f), source_dir) for dp, _, fn in os.walk(source_dir) for f in sorted(fn) if f.lower().endswith('.md') and not f.startswith('._')]
    
    update_task_status(task_id, 'READY', preview_files=preview_files)
    return jsonify({'task_id': task_id, 'preview_files': preview_files})

def generate_preview_pdf(task_id, rel_path, style_options, content_modifier=None):
    import pypandoc
    import weasyprint

    with TASKS_LOCK: task_dir = TASKS.get(task_id, {}).get('task_dir')
    if not task_dir: raise FileNotFoundError("Invalid task ID.")
    
    source_file_abs = os.path.join(task_dir, 'source', rel_path)
    if not os.path.normpath(source_file_abs).startswith(os.path.normpath(os.path.join(task_dir, 'source'))):
        raise PermissionError("Path traversal attempt detected.")

    md_content = read_file_with_fallback(source_file_abs)
    
    if content_modifier: # For translation
        md_content = content_modifier(md_content)

    processed_md = preprocess_markdown_images(md_content, os.path.dirname(source_file_abs))
    html_body = pypandoc.convert_text(source=processed_md, to='html', format='markdown+latex_macros', extra_args=[f'--highlight-style={style_options.get("code_theme", "kate")}'])
    css = weasyprint.CSS(string=get_css_style(style_options))
    return weasyprint.HTML(string=f'<html><body>{html_body}</body></html>').write_pdf(stylesheets=[css])

@app.route('/preview/original', methods=['POST'])
def preview_original():
    try:
        data = request.get_json()
        pdf_bytes = generate_preview_pdf(data['task_id'], data['preview_file'], data['style_options'])
        return Response(pdf_bytes, mimetype='application/pdf')
    except Exception as e:
        traceback.print_exc()
        return Response(f"Error: {e}", status=500, mimetype='text/plain')

@app.route('/preview/translated', methods=['POST'])
def preview_translated():
    try:
        data = request.get_json()
        
        def translate_modifier(content):
            return translate_text_via_api(data['task_id'], content, data['target_language'], TRANSLATION_PROMPT, log_id=data['preview_file'])

        pdf_bytes = generate_preview_pdf(data['task_id'], data['preview_file'], data['style_options'], content_modifier=translate_modifier)
        return Response(pdf_bytes, mimetype='application/pdf')
    except Exception as e:
        traceback.print_exc()
        return Response(f"Error: {e}", status=500, mimetype='text/plain')

@app.route('/start_conversion', methods=['POST'])
def start_conversion():
    data = request.get_json()
    task_id = data.get('task_id')
    if not task_id or task_id not in TASKS: return jsonify({'error': 'Invalid Task ID'}), 404
    
    mode_text = {'translated': '仅译文', 'original': '仅原文', 'bilingual': '双语对照 + 单独译文'}.get(data.get('export_mode'), '未知')
    log_message = f"任务已启动 (ID: {task_id}, 模式: {mode_text})"
    update_task_status(task_id, 'RUNNING', progress=0, log=log_message)
    
    threading.Thread(target=run_conversion_thread, args=(
        task_id, 
        data.get('style_options', {}), 
        data.get('target_language'),
        data.get('export_mode', 'translated')
    )).start()
    return jsonify({'task_id': task_id, 'message': 'Process started.'})

@app.route('/<action>/<task_id>', methods=['POST'])
def control_task_endpoint(action, task_id):
    if task_id not in TASKS:
        return jsonify({'error': 'Invalid Task ID'}), 404
    
    if action == 'pause':
        update_task_status(task_id, state='PAUSED', log='任务已暂停。')
    elif action == 'resume':
        update_task_status(task_id, state='RUNNING', log='任务已恢复。')
    elif action == 'stop':
        update_task_status(task_id, state='STOPPING') # Thread will set final state
    else:
        return jsonify({'error': 'Invalid action'}), 400
        
    return jsonify({'message': f'Task {action} signal sent.'})

@app.route('/status/<task_id>')
def task_status(task_id):
    logs = get_and_clear_logs(task_id)
    with TASKS_LOCK: task = TASKS.get(task_id, {})
    return jsonify({'state': task.get('state', 'UNKNOWN'), 'progress': task.get('progress', 0), 'logs': logs, 'error': task.get('error'), 'result_url': task.get('result_url')})

@app.route('/download/<task_id>')
def download_result(task_id):
    with TASKS_LOCK: task_info = TASKS.get(task_id)
    if not task_info or task_info.get('state') != 'SUCCESS': return "Task not found or not completed.", 404
    task_dir = task_info.get('task_dir')
    zip_filename = f"Translated_Results_{task_id[:8]}.zip"
    return send_from_directory(task_dir, zip_filename, as_attachment=True)

def check_dependencies():
    print("="*20 + " Performing Startup Environment Check " + "="*20)
    all_ok = True
    try:
        import pypandoc
        pypandoc.get_pandoc_version()
        print("[✔] Pandoc dependency found.")
    except (OSError, ImportError):
        print("[❌] ERROR: Pandoc installation not found!")
        print("    Please install it from: https://pandoc.org/installing.html")
        all_ok = False
    
    for module in ['weasyprint', 'pandas']:
        try:
            __import__(module)
            print(f"[✔] {module.capitalize()} dependency found.")
        except ImportError:
            print(f"[❌] ERROR: {module.capitalize()} not found! Please run: pip install {module}")
            all_ok = False
    return all_ok

if __name__ == '__main__':
    static_dir = os.path.join(BASE_DIR, 'static')
    os.makedirs(static_dir, exist_ok=True)
    favicon_path = os.path.join(static_dir, 'favicon.ico')
    if not os.path.exists(favicon_path):
        with open(favicon_path, 'wb') as f:
            f.write(base64.b64decode('AAABAAEAEBAQAAEABAAoAQAAFgAAACgAAAAQAAAAIAAAAAEABAAAAAAAgAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAA/4QAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAEREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREREQAAEQARERERERERARERABEREREREREQARAAAREREREAAAEQAAEQAREREREQARERERABERERARAAARERAAARERAAARERERERERERERERERERERERERERERERERERERERERERH//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA'))

    if check_dependencies():
        print("="*60)
        print("【 AI Document Translation Service v8 】")
        print(f"All output files will be saved in: {OUTPUT_DIR}")
        print("Access the service at: http://127.0.0.1:5000")
        print("="*60)
        app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
    else:
        print("\nDependency check failed. The service cannot start.")
