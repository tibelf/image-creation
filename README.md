# ComfyUI批量图片生成工具

这个Python脚本可以调用ComfyUI API来批量执行工作流并生成图片。

## 功能特点

- 🚀 批量处理多个提示词
- 🔄 自动更新工作流中的正面和负面提示词
- 📁 自动保存生成的图片到指定目录
- ⚡ WebSocket实时通信
- 🛡️ 完善的错误处理机制
- 📝 详细的执行日志

## 安装依赖

```bash
pip install -r requirements.txt
```

## 使用方法

### 1. 准备文件

确保你有以下文件：
- `~/Downloads/lora.json` - ComfyUI工作流文件
- `prompts.json` - 提示词文件

### 2. 运行脚本

基本用法：
```bash
python comfyui_batch_generator.py
```

自定义参数：
```bash
python comfyui_batch_generator.py \
  --workflow ~/Downloads/lora.json \
  --prompts prompts.json \
  --output output \
  --server 127.0.0.1:8188
```

### 3. 参数说明

- `--workflow, -w`: ComfyUI工作流文件路径 (默认: ~/Downloads/lora.json)
- `--prompts, -p`: 提示词JSON文件路径 (默认: prompts.json)
- `--output, -o`: 输出目录 (默认: output)
- `--server, -s`: ComfyUI服务器地址 (默认: 127.0.0.1:8188)

## 提示词文件格式

`prompts.json` 文件格式示例：

```json
{
  "prompts": [
    {
      "id": 1,
      "positive": "beautiful anime girl, long hair, blue eyes, school uniform, detailed face, high quality, masterpiece",
      "negative": "nsfw, worst quality, low quality, bad anatomy, bad hands, missing fingers, blurry"
    },
    {
      "id": 2,
      "positive": "cat girl, cute, neko ears, sitting, indoor scene, soft lighting, kawaii style",
      "negative": "nsfw, worst quality, low quality, bad anatomy, deformed, blurry"
    }
  ]
}
```

## 输出

生成的图片将保存在指定的输出目录中，文件名格式为：
```
prompt_{id}_node_{node_id}_{image_index}.png
```

## 注意事项

1. 确保ComfyUI服务正在运行（通常在 http://127.0.0.1:8188）
2. 工作流文件必须是有效的ComfyUI工作流JSON格式
3. 确保有足够的磁盘空间存储生成的图片
4. 如果遇到网络错误，脚本会跳过当前提示词继续处理下一个

## 故障排除

- **连接错误**: 检查ComfyUI是否正在运行，端口是否正确
- **工作流错误**: 确认工作流文件格式正确，模型文件存在
- **权限错误**: 确保输出目录有写入权限