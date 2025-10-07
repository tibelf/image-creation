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
        """验证UI导出格式工作流的完整性"""
        if not isinstance(workflow, dict):
            return False, "工作流必须是字典格式"
        
        # 检查是否包含nodes数组
        if 'nodes' not in workflow:
            return False, "工作流缺少nodes数组"
        
        nodes = workflow['nodes']
        if not isinstance(nodes, list):
            return False, "nodes必须是数组格式"
        
        # 验证每个节点
        for i, node in enumerate(nodes):
            if not isinstance(node, dict):
                return False, f"节点 {i} 不是字典格式"
            
            if 'id' not in node:
                return False, f"节点 {i} 缺少id属性"
            
            if 'type' not in node:
                return False, f"节点 {node.get('id', i)} 缺少type属性"
        
        return True, "UI格式工作流验证通过"
    
    def find_text_encode_nodes(self, workflow):
        """查找UI格式工作流中的文本编码节点"""
        text_nodes = {}
        
        nodes = workflow.get('nodes', [])
        for node in nodes:
            if (isinstance(node, dict) and 
                node.get('type') == 'CLIPTextEncode' and 
                'widgets_values' in node and 
                len(node['widgets_values']) > 0):
                
                node_id = node.get('id')
                current_text = str(node['widgets_values'][0]).lower()
                text_nodes[node_id] = {
                    'node': node,
                    'text': node['widgets_values'][0],
                    'is_negative': any(neg_word in current_text for neg_word in 
                                     ['nsfw', 'worst', 'low quality', 'bad', 'negative', 'ugly'])
                }
        
        return text_nodes
    
    def update_workflow_prompt(self, workflow, positive_prompt, negative_prompt, positive_node_id=6, negative_node_id=7):
        """更新UI格式工作流中的提示词，直接通过节点ID指定"""
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
        
        # 直接通过节点ID更新提示词
        updated_count = 0
        nodes = workflow_copy.get('nodes', [])
        
        for node in nodes:
            if not isinstance(node, dict) or 'id' not in node:
                continue
            
            node_id = node.get('id')
            
            # 检查是否为CLIPTextEncode节点且有widgets_values
            if (node.get('type') == 'CLIPTextEncode' and 
                'widgets_values' in node and 
                len(node['widgets_values']) > 0):
                
                try:
                    if node_id == positive_node_id:
                        node['widgets_values'][0] = positive_prompt
                        print(f"更新正面提示词节点: {node_id}")
                        updated_count += 1
                    elif node_id == negative_node_id:
                        node['widgets_values'][0] = negative_prompt
                        print(f"更新负面提示词节点: {node_id}")
                        updated_count += 1
                except Exception as e:
                    print(f"更新节点 {node_id} 时出错: {str(e)}")
        
        if updated_count == 0:
            print(f"警告: 未找到ID为 {positive_node_id} 或 {negative_node_id} 的CLIPTextEncode节点")
        else:
            print(f"成功更新 {updated_count} 个提示词节点")
        
        # 最终验证
        is_valid, message = self.validate_workflow(workflow_copy)
        if not is_valid:
            raise ValueError(f"更新后工作流验证失败: {message}")
        
        return workflow_copy
    
    def convert_ui_to_api_format(self, ui_workflow):
        """将UI导出格式转换为API工作流格式"""
        api_workflow = {}
        links = ui_workflow.get('links', [])
        
        # 创建链接映射：link_id -> (source_node_id, source_output_index, target_node_id, target_input_name)
        link_map = {}
        for link in links:
            if len(link) >= 6:
                link_id, source_node, source_output, target_node, target_input, link_type = link[:6]
                link_map[link_id] = {
                    'source_node': source_node,
                    'source_output': source_output,
                    'target_node': target_node,
                    'target_input': target_input
                }
        
        nodes = ui_workflow.get('nodes', [])
        for node in nodes:
            if not isinstance(node, dict) or 'id' not in node:
                continue
            
            node_id = str(node['id'])
            
            # 构建API格式的节点
            api_node = {
                'class_type': node.get('type', ''),
                'inputs': {}
            }
            
            # 处理连接输入
            if 'inputs' in node:
                for input_item in node['inputs']:
                    input_name = input_item.get('name', '')
                    link_id = input_item.get('link')
                    
                    if link_id is not None and link_id in link_map:
                        link_info = link_map[link_id]
                        api_node['inputs'][input_name] = [str(link_info['source_node']), link_info['source_output']]
            
            # 处理widgets_values
            if 'widgets_values' in node and node['widgets_values']:
                self._map_widgets_to_inputs(node, api_node)
            
            # 特殊处理CLIPTextEncode节点
            if node.get('type') == 'CLIPTextEncode' and 'widgets_values' in node and len(node['widgets_values']) > 0:
                api_node['inputs']['text'] = node['widgets_values'][0]
            
            api_workflow[node_id] = api_node
        
        return api_workflow
    
    def _map_widgets_to_inputs(self, ui_node, api_node):
        """将widgets_values映射到API格式的inputs"""
        node_type = ui_node.get('type', '')
        widgets = ui_node.get('widgets_values', [])
        
        # 根据节点类型进行映射（这里只实现常见的几种）
        if node_type == 'KSampler' and len(widgets) >= 7:
            api_node['inputs'].update({
                'seed': widgets[0],
                'control_after_generate': widgets[1],  
                'steps': widgets[2],
                'cfg': widgets[3],
                'sampler_name': widgets[4],
                'scheduler': widgets[5],
                'denoise': widgets[6]
            })
        elif node_type == 'EmptyLatentImage' and len(widgets) >= 3:
            api_node['inputs'].update({
                'width': widgets[0],
                'height': widgets[1], 
                'batch_size': widgets[2]
            })
        elif node_type == 'CheckpointLoaderSimple' and len(widgets) >= 1:
            api_node['inputs']['ckpt_name'] = widgets[0]
        elif node_type == 'LoraLoader' and len(widgets) >= 3:
            api_node['inputs'].update({
                'lora_name': widgets[0],
                'strength_model': widgets[1],
                'strength_clip': widgets[2]
            })
    
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
                    
                    # 将UI格式转换为API格式
                    print("转换工作流格式...")
                    api_workflow = self.convert_ui_to_api_format(updated_workflow)
                    
                    # 保存API格式的工作流（调试用）
                    self.save_debug_workflow(api_workflow, prompt_data['id'], "api_format")
                    
                    # 生成图片
                    print("正在生成图片...")
                    try:
                        images = self.client.get_images(ws, api_workflow)
                        
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