import os
import uuid
import time
import json
from flask import Flask, request, jsonify, render_template, make_response, send_file
from flask_sqlalchemy import SQLAlchemy
from functools import wraps
from datetime import datetime
import threading

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['TEMP_FOLDER'] = os.path.join(app.config['UPLOAD_FOLDER'], 'temp')  # 新增：临时区块文件夹
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024 * 1024  # 1GB
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///files.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# 确保上传目录和临时目录存在
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['TEMP_FOLDER'], exist_ok=True)  # 新增：创建临时文件夹

# 上传进度跟踪
upload_progress = {}
progress_lock = threading.Lock()

# 数据库模型
class File(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    file_id = db.Column(db.String(36), unique=True, nullable=False)
    original_filename = db.Column(db.String(256), nullable=False)
    saved_filename = db.Column(db.String(256), nullable=False)
    file_path = db.Column(db.String(512), nullable=False)
    file_size = db.Column(db.Integer, nullable=False)
    uploaded_size = db.Column(db.Integer, default=0)
    upload_time = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(20), default='uploading')  # pending, uploading, success, error

    def to_dict(self):
        return {
            'id': self.id,
            'file_id': self.file_id,
            'original_filename': self.original_filename,
            'saved_filename': self.saved_filename,
            'file_path': self.file_path,
            'file_size': self.file_size,
            'uploaded_size': self.uploaded_size,
            'upload_time': self.upload_time,
            'status': self.status
        }

# 创建数据库表
with app.app_context():
    db.create_all()

def generate_unique_filename(filename):
    """生成唯一的文件名"""
    ext = os.path.splitext(filename)[1]
    return f"{uuid.uuid4().hex}{ext}"

"""允许所有文件类型"""
def allowed_file(filename):
    """允许所有文件类型"""
    return True

"""渲染上传页面"""
@app.route('/')
def index():
    """渲染上传页面"""
    return render_template('index.html')

"""初始化大文件上传"""
@app.route('/upload/init', methods=['POST'])
def init_upload():
    try:
        data = request.get_json()
        filename = data.get('filename')
        file_size = data.get('fileSize', 0)
        
        if not filename:
            return jsonify({'error': '文件名不能为空'}), 400
            
        file_id = str(uuid.uuid4())
        saved_filename = generate_unique_filename(filename)
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], saved_filename)
        
        # 创建文件记录
        upload_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        new_file = File(
            file_id=file_id,
            original_filename=filename,
            saved_filename=saved_filename,
            file_path=file_path,
            file_size=file_size,
            upload_time=upload_time,
            status='pending'
        )
        db.session.add(new_file)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'fileId': file_id,
            'chunkSize': 5 * 1024 * 1024  # 5MB块大小
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

"""处理文件分块上传"""
@app.route('/upload/chunk', methods=['POST'])
def upload_chunk():
    try:
        file_id = request.form.get('fileId')
        chunk_index = int(request.form.get('chunkIndex', 0))
        total_chunks = int(request.form.get('totalChunks', 1))
        
        if not file_id:
            return jsonify({'error': '文件ID不能为空'}), 400
            
        if 'chunk' not in request.files:
            return jsonify({'error': '未找到文件块'}), 400
            
        # 获取文件记录
        file_record = File.query.filter_by(file_id=file_id).first()
        if not file_record:
            return jsonify({'error': '文件记录不存在'}), 404
            
        # 保存块文件到temp文件夹  # 修改：存储路径变更
        chunk = request.files['chunk']
        chunk_filename = f"{file_record.saved_filename}.part{chunk_index}"
        chunk_path = os.path.join(app.config['TEMP_FOLDER'], chunk_filename)  # 修改：使用临时文件夹
        chunk.save(chunk_path)
        
        # 更新已上传大小
        chunk_size = os.path.getsize(chunk_path)
        file_record.uploaded_size += chunk_size
        file_record.status = 'uploading'
        db.session.commit()
        
        # 检查是否所有块都已上传
        if chunk_index == total_chunks - 1:
            # 合并所有块
            with open(file_record.file_path, 'wb') as outfile:
                for i in range(total_chunks):
                    part_filename = f"{file_record.saved_filename}.part{i}"
                    part_path = os.path.join(app.config['TEMP_FOLDER'], part_filename)  # 修改：从临时文件夹读取
                    with open(part_path, 'rb') as infile:
                        outfile.write(infile.read())
                    os.remove(part_path)  # 删除临时块文件
            
            # 更新文件状态
            file_record.status = 'success'
            db.session.commit()
            
            return jsonify({
                'success': True,
                'complete': True,
                'fileId': file_id
            })
            
        return jsonify({
            'success': True,
            'complete': False,
            'chunkIndex': chunk_index,
            'progress': int((file_record.uploaded_size / file_record.file_size) * 100) if file_record.file_size > 0 else 0
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

"""获取上传进度"""
@app.route('/upload/progress/<file_id>')
def get_upload_progress(file_id):
    try:
        file_record = File.query.filter_by(file_id=file_id).first()
        if not file_record:
            return jsonify({'progress': -1})
            
        progress = 0
        if file_record.file_size > 0:
            progress = int((file_record.uploaded_size / file_record.file_size) * 100)
            
        return jsonify({
            'progress': progress,
            'status': file_record.status
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

"""查询所有文件信息（按上传时间倒序）"""
def get_all_files():
    """查询所有文件信息（按上传时间倒序）"""
    files = File.query.order_by(File.upload_time.desc()).all()
    return [file.to_dict() for file in files]

"""渲染文件列表页面，展示所有上传的文件"""
@app.route('/view')
def view_files():
    """渲染文件列表页面，展示所有上传的文件"""
    files = get_all_files()
    return render_template('view.html', files=files)

"""文件下载接口（用于/view页面的下载功能）"""
@app.route('/download/<saved_filename>')
def download_file(saved_filename):
    """文件下载接口（用于/view页面的下载功能）"""
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], saved_filename)
    if not os.path.exists(file_path):
        return jsonify({'error': '文件不存在'}), 404
    
    # 从数据库查询原始文件名（用于下载时显示）
    file_record = File.query.filter_by(saved_filename=saved_filename).first()
    original_filename = file_record.original_filename if file_record else saved_filename
    
    # 解决中文文件名编码问题
    from urllib.parse import quote
    encoded_filename = quote(original_filename, encoding='utf-8')
    
    return send_file(
        file_path,
        as_attachment=True,
        download_name=encoded_filename,
        mimetype='application/octet-stream'
    )

"""删除文件"""
@app.route('/delete/<saved_filename>', methods=['DELETE'])
def delete_file(saved_filename):
    try:
        file_record = File.query.filter_by(saved_filename=saved_filename).first()
        if not file_record:
            return jsonify({'error': '文件不存在'}), 404
            
        # 删除文件
        if os.path.exists(file_record.file_path):
            os.remove(file_record.file_path)
            
        # 删除数据库记录
        db.session.delete(file_record)
        db.session.commit()
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=False,port=50000,host="0.0.0.0")
