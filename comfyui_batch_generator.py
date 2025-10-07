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
import copy

class ComfyUIClient:
    def __init__(self, server_address="127.0.0.1:80"):
        self.server_address = server_address
        self.client_id = str(uuid.uuid4())
        
    def queue_prompt(self, prompt):
        """将提示词发送到ComfyUI队列"""
        p = {"prompt": prompt, "client_id": self.client_id}
        data = json.dumps(p).encode('utf-8')
        req = urllib.request.Request(f"http://{self.server_address}/prompt", data=data)
        req.add_header('Content-Type', 'application/json')
        
        try:
            response = urllib.request.urlopen(req)
            result = json.loads(response.read())
            return result
        except urllib.error.HTTPError as e:
            error_body = e.read().decode('utf-8')
            print(f"HTTP错误 {e.code}: {e.reason}")
            print(f"错误详情: {error_body}")
            try:
                error_json = json.loads(error_body)
                if 'error' in error_json:
                    print(f"ComfyUI错误信息: {error_json['error']}")
            except:
                pass
            raise
        except Exception as e:
            print(f"发送提示词到队列时出错: {str(e)}")
            raise

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
    def __init__(self, workflow_path, prompts_path, output_dir="output", server_address="127.0.0.1:8188", debug=False):
        self.workflow_path = Path(workflow_path)
        self.prompts_path = Path(prompts_path)
        self.output_dir = Path(output_dir)
        self.server_address = server_address
        self.client = ComfyUIClient(server_address)
        self.debug = debug
        
        # 创建输出目录
        self.output_dir.mkdir(exist_ok=True)
        
        # 创建调试目录
        if self.debug:
            self.debug_dir = self.output_dir / "debug"
            self.debug_dir.mkdir(exist_ok=True)
        
    def load_workflow(self):
        """加载ComfyUI工作流"""
        with open(self.workflow_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def load_prompts(self):
        """加载提示词列表"""
        with open(self.prompts_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def validate_workflow(self, workflow):
        """验证工作流的完整性"""
        if not isinstance(workflow, dict):
            return False, "工作流必须是字典格式"
        
        for node_id, node in workflow.items():
            # 检查节点ID是否为字符串格式的数字
            if not isinstance(node_id, str) or not node_id.isdigit():
                return False, f"节点ID格式错误: {node_id}"
            
            # 检查节点是否为字典且包含必要属性
            if not isinstance(node, dict):
                return False, f"节点 {node_id} 不是字典格式"
            
            if 'class_type' not in node:
                return False, f"节点 {node_id} 缺少 class_type 属性"
        
        return True, "工作流验证通过"
    
    def find_text_encode_nodes(self, workflow):
        """查找工作流中的文本编码节点"""
        text_nodes = {}
        
        for node_id, node in workflow.items():
            if (isinstance(node, dict) and 
                node.get('class_type') == 'CLIPTextEncode' and 
                'inputs' in node and 
                'text' in node['inputs']):
                
                current_text = str(node['inputs']['text']).lower()
                text_nodes[node_id] = {
                    'node': node,
                    'text': node['inputs']['text'],
                    'is_negative': any(neg_word in current_text for neg_word in 
                                     ['nsfw', 'worst', 'low quality', 'bad', 'negative', 'ugly'])
                }
        
        return text_nodes
    
    def update_workflow_prompt(self, workflow, positive_prompt, negative_prompt):
        """更新工作流中的提示词，保持原有结构完整性"""
        # 首先验证原工作流
        is_valid, message = self.validate_workflow(workflow)
        if not is_valid:
            raise ValueError(f"原工作流验证失败: {message}")
        
        # 创建深拷贝
        workflow_copy = copy.deepcopy(workflow)
        
        # 验证拷贝后的工作流
        is_valid, message = self.validate_workflow(workflow_copy)
        if not is_valid:
            raise ValueError(f"拷贝后工作流验证失败: {message}")
        
        # 查找文本编码节点
        text_nodes = self.find_text_encode_nodes(workflow_copy)
        
        if not text_nodes:
            print("警告: 未找到任何CLIPTextEncode节点")
            return workflow_copy
        
        # 更新提示词
        updated_count = 0
        for node_id, node_info in text_nodes.items():
            try:
                if node_info['is_negative']:
                    workflow_copy[node_id]['inputs']['text'] = negative_prompt
                    print(f"更新负面提示词节点: {node_id}")
                else:
                    workflow_copy[node_id]['inputs']['text'] = positive_prompt  
                    print(f"更新正面提示词节点: {node_id}")
                updated_count += 1
            except Exception as e:
                print(f"更新节点 {node_id} 时出错: {str(e)}")
        
        print(f"成功更新 {updated_count} 个提示词节点")
        
        # 最终验证
        is_valid, message = self.validate_workflow(workflow_copy)
        if not is_valid:
            raise ValueError(f"更新后工作流验证失败: {message}")
        
        return workflow_copy
    
    def save_debug_workflow(self, workflow, prompt_id, stage=""):
        """保存调试用的工作流文件"""
        if not self.debug:
            return
        
        filename = f"workflow_prompt_{prompt_id}_{stage}.json"
        filepath = self.debug_dir / filename
        
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(workflow, f, indent=2, ensure_ascii=False)
            print(f"调试工作流已保存: {filepath}")
        except Exception as e:
            print(f"保存调试工作流失败: {str(e)}")
    
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
                    if self.debug:
                        print(f"负面提示词: {prompt_data['negative'][:50]}...")
                    
                    # 保存原始工作流（调试用）
                    self.save_debug_workflow(workflow, prompt_data['id'], "original")
                    
                    # 更新工作流
                    print("更新工作流中的提示词...")
                    updated_workflow = self.update_workflow_prompt(
                        workflow, 
                        prompt_data['positive'], 
                        prompt_data['negative']
                    )
                    
                    # 保存修改后的工作流（调试用）
                    self.save_debug_workflow(updated_workflow, prompt_data['id'], "updated")
                    
                    # 生成图片
                    print("正在生成图片...")
                    try:
                        images = self.client.get_images(ws, updated_workflow)
                        
                        # 保存图片
                        saved_count = 0
                        for node_id in images:
                            for j, image_data in enumerate(images[node_id]):
                                filename = f"prompt_{prompt_data['id']}_node_{node_id}_{j}.png"
                                image_path = self.output_dir / filename
                                
                                with open(image_path, "wb") as f:
                                    f.write(image_data)
                                
                                print(f"图片已保存: {image_path}")
                                saved_count += 1
                        
                        if saved_count == 0:
                            print("警告: 没有生成任何图片")
                        else:
                            print(f"提示词 {prompt_data['id']} 处理完成，生成 {saved_count} 张图片")
                    
                    except Exception as gen_error:
                        print(f"生成图片时出错: {str(gen_error)}")
                        if self.debug:
                            import traceback
                            traceback.print_exc()
                        continue
                    
                except Exception as e:
                    print(f"处理提示词 {prompt_data['id']} 时出错: {str(e)}")
                    if self.debug:
                        import traceback
                        traceback.print_exc()
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
    parser.add_argument('--debug', '-d',
                       action='store_true',
                       help='启用调试模式，保存工作流文件和详细错误信息')
    
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
    if args.debug:
        print("调试模式: 已启用")
    
    # 创建生成器并开始批量生成
    generator = ComfyUIBatchGenerator(
        workflow_path=workflow_path,
        prompts_path=prompts_path,
        output_dir=args.output,
        server_address=args.server,
        debug=args.debug
    )
    
    generator.generate_batch()

if __name__ == "__main__":
    main()