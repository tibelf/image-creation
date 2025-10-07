#!/usr/bin/env python3
"""
ComfyUI批量图片生成脚本
使用ComfyUI API执行工作流并批量生成图片
"""

import json
import requests
import websocket
import uuid
import urllib.request
import urllib.parse
import time
import os
from pathlib import Path
import argparse
import sys

class ComfyUIClient:
    def __init__(self, server_address="127.0.0.1:80"):
        self.server_address = server_address
        self.client_id = str(uuid.uuid4())
        
    def queue_prompt(self, prompt):
        """将提示词发送到ComfyUI队列"""
        p = {"prompt": prompt, "client_id": self.client_id}
        data = json.dumps(p).encode('utf-8')
        req = urllib.request.Request(f"http://{self.server_address}/prompt", data=data)
        return json.loads(urllib.request.urlopen(req).read())

    def get_image(self, filename, subfolder, folder_type):
        """从ComfyUI获取生成的图片"""
        data = {"filename": filename, "subfolder": subfolder, "type": folder_type}
        url_values = urllib.parse.urlencode(data)
        with urllib.request.urlopen(f"http://{self.server_address}/view?{url_values}") as response:
            return response.read()

    def get_history(self, prompt_id):
        """获取提示词执行历史"""
        with urllib.request.urlopen(f"http://{self.server_address}/history/{prompt_id}") as response:
            return json.loads(response.read())

    def get_images(self, ws, prompt):
        """执行工作流并获取生成的图片"""
        prompt_id = self.queue_prompt(prompt)['prompt_id']
        output_images = {}
        
        while True:
            out = ws.recv()
            if isinstance(out, str):
                message = json.loads(out)
                if message['type'] == 'executing':
                    data = message['data']
                    if data['node'] is None and data['prompt_id'] == prompt_id:
                        break
            else:
                continue

        history = self.get_history(prompt_id)[prompt_id]
        for node_id in history['outputs']:
            node_output = history['outputs'][node_id]
            if 'images' in node_output:
                images_output = []
                for image in node_output['images']:
                    image_data = self.get_image(image['filename'], image['subfolder'], image['type'])
                    images_output.append(image_data)
                output_images[node_id] = images_output

        return output_images

class ComfyUIBatchGenerator:
    def __init__(self, workflow_path, prompts_path, output_dir="output", server_address="127.0.0.1:8188"):
        self.workflow_path = Path(workflow_path)
        self.prompts_path = Path(prompts_path)
        self.output_dir = Path(output_dir)
        self.server_address = server_address
        self.client = ComfyUIClient(server_address)
        
        # 创建输出目录
        self.output_dir.mkdir(exist_ok=True)
        
    def load_workflow(self):
        """加载ComfyUI工作流"""
        with open(self.workflow_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def load_prompts(self):
        """加载提示词列表"""
        with open(self.prompts_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def update_workflow_prompt(self, workflow, positive_prompt, negative_prompt):
        """更新工作流中的提示词"""
        workflow_copy = json.deepcopy(workflow)
        
        # 找到正面和负面提示词节点并更新
        for node_id, node in workflow_copy.items():
            if isinstance(node, dict) and 'class_type' in node:
                if node['class_type'] == 'CLIPTextEncode':
                    # 根据输入链接判断是正面还是负面提示词
                    if 'inputs' in node and 'text' in node['inputs']:
                        current_text = node['inputs']['text']
                        # 简单的启发式判断：包含负面词汇的通常是负面提示词
                        if any(neg_word in current_text.lower() for neg_word in ['nsfw', 'worst', 'low quality', 'bad']):
                            node['inputs']['text'] = negative_prompt
                        else:
                            node['inputs']['text'] = positive_prompt
        
        return workflow_copy
    
    def generate_batch(self):
        """批量生成图片"""
        try:
            # 加载工作流和提示词
            print("加载工作流和提示词...")
            workflow = self.load_workflow()
            prompts_data = self.load_prompts()
            
            # 连接WebSocket
            print(f"连接到ComfyUI服务器: {self.server_address}")
            ws = websocket.WebSocket()
            ws.connect(f"ws://{self.server_address}/ws?clientId={self.client.client_id}")
            
            total_prompts = len(prompts_data['prompts'])
            print(f"开始批量生成，共{total_prompts}个提示词")
            
            for i, prompt_data in enumerate(prompts_data['prompts'], 1):
                try:
                    print(f"\n[{i}/{total_prompts}] 处理提示词 ID: {prompt_data['id']}")
                    print(f"正面提示词: {prompt_data['positive'][:50]}...")
                    
                    # 更新工作流
                    updated_workflow = self.update_workflow_prompt(
                        workflow, 
                        prompt_data['positive'], 
                        prompt_data['negative']
                    )
                    
                    # 生成图片
                    print("正在生成图片...")
                    images = self.client.get_images(ws, updated_workflow)
                    
                    # 保存图片
                    for node_id in images:
                        for j, image_data in enumerate(images[node_id]):
                            filename = f"prompt_{prompt_data['id']}_node_{node_id}_{j}.png"
                            image_path = self.output_dir / filename
                            
                            with open(image_path, "wb") as f:
                                f.write(image_data)
                            
                            print(f"图片已保存: {image_path}")
                    
                    print(f"提示词 {prompt_data['id']} 处理完成")
                    
                except Exception as e:
                    print(f"处理提示词 {prompt_data['id']} 时出错: {str(e)}")
                    continue
            
            print(f"\n批量生成完成！图片保存在: {self.output_dir}")
            
        except Exception as e:
            print(f"批量生成过程中发生错误: {str(e)}")
            sys.exit(1)
        finally:
            try:
                ws.close()
            except:
                pass

def main():
    parser = argparse.ArgumentParser(description='ComfyUI批量图片生成工具')
    parser.add_argument('--workflow', '-w', 
                       default='~/Downloads/lora.json',
                       help='ComfyUI工作流文件路径 (默认: ~/Downloads/lora.json)')
    parser.add_argument('--prompts', '-p',
                       default='prompts.json', 
                       help='提示词JSON文件路径 (默认: prompts.json)')
    parser.add_argument('--output', '-o',
                       default='output',
                       help='输出目录 (默认: output)')
    parser.add_argument('--server', '-s',
                       default='127.0.0.1:8188',
                       help='ComfyUI服务器地址 (默认: 127.0.0.1:8188)')
    
    args = parser.parse_args()
    
    # 展开路径
    workflow_path = Path(args.workflow).expanduser()
    prompts_path = Path(args.prompts)
    
    # 检查文件是否存在
    if not workflow_path.exists():
        print(f"错误: 工作流文件不存在: {workflow_path}")
        sys.exit(1)
        
    if not prompts_path.exists():
        print(f"错误: 提示词文件不存在: {prompts_path}")
        sys.exit(1)
    
    print("ComfyUI批量图片生成工具")
    print(f"工作流文件: {workflow_path}")
    print(f"提示词文件: {prompts_path}")
    print(f"输出目录: {args.output}")
    print(f"服务器地址: {args.server}")
    
    # 创建生成器并开始批量生成
    generator = ComfyUIBatchGenerator(
        workflow_path=workflow_path,
        prompts_path=prompts_path,
        output_dir=args.output,
        server_address=args.server
    )
    
    generator.generate_batch()

if __name__ == "__main__":
    main()