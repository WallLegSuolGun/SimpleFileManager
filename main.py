import os
import uuid
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_file
from flask_sqlalchemy import SQLAlchemy
from urllib.parse import quote

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key'
app.config['UPLOAD_FOLDER'] = os.path.join(os.getcwd(), 'uploads')
app.config['TEMP_FOLDER'] = os.path.join(app.config['UPLOAD_FOLDER'], 'temp')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///files.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# 创建存储目录
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['TEMP_FOLDER'], exist_ok=True)

db = SQLAlchemy(app)

# 标签模型
class Tag(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    files = db.relationship('File', secondary='file_tag', backref='tags', cascade="all, delete")

# 多对多关联表
file_tag = db.Table('file_tag',
    db.Column('file_id', db.Integer, db.ForeignKey('file.id'), primary_key=True),
    db.Column('tag_id', db.Integer, db.ForeignKey('tag.id'), primary_key=True)
)

# 文件模型
class File(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    file_id = db.Column(db.String(36), unique=True, nullable=False)
    original_filename = db.Column(db.String(256), nullable=False)
    saved_filename = db.Column(db.String(256), nullable=False)
    file_path = db.Column(db.String(512), nullable=False)
    file_size = db.Column(db.Integer, nullable=False)
    uploaded_size = db.Column(db.Integer, default=0)
    upload_time = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(20), default='pending')  # pending, uploading, success, error

    def to_dict(self):
        return {
            'id': self.id,
            'file_id': self.file_id,
            'original_filename': self.original_filename,
            'saved_filename': self.saved_filename,
            'file_size': self.file_size,
            'upload_time': self.upload_time,
            'status': self.status,
            'tags': [tag.name for tag in self.tags]
        }

# 生成唯一文件名
def generate_unique_filename(filename):
    ext = os.path.splitext(filename)[1]
    return f"{uuid.uuid4().hex}{ext}"

# 允许所有文件类型
def allowed_file(filename):
    return True

# 渲染上传页面
@app.route('/')
def index():
    tags = Tag.query.all()
    return render_template('index.html', tags=tags)

# 初始化大文件上传
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

# 处理文件分块上传
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
            
        # 保存块文件到temp文件夹
        chunk = request.files['chunk']
        chunk_filename = f"{file_record.saved_filename}.part{chunk_index}"
        chunk_path = os.path.join(app.config['TEMP_FOLDER'], chunk_filename)
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
                    part_path = os.path.join(app.config['TEMP_FOLDER'], part_filename)
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

# 获取文件标签
@app.route('/file/<file_id>/tags', methods=['GET'])
def get_file_tags(file_id):
    # 尝试先按file_id查询，如果失败再尝试按id查询
    file = File.query.filter_by(file_id=file_id).first()
    if not file:
        try:
            # 如果是数字ID，尝试按主键id查询
            file_id_int = int(file_id)
            file = File.query.get(file_id_int)
        except ValueError:
            pass
    
    if not file:
        return jsonify({'error': '文件不存在'}), 404
        
    return jsonify({
        'success': True,
        'tags': [{'id': tag.id, 'name': tag.name} for tag in file.tags]
    })

# 获取上传进度
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

# 查询所有文件
def get_all_files():
    return File.query.order_by(File.upload_time.desc()).all()

# 渲染文件列表页面
@app.route('/view')
def view_files():
    files = get_all_files()
    tags = Tag.query.all()
    return render_template('view.html', files=files, tags=tags)

# 文件下载接口
@app.route('/download/<saved_filename>')
def download_file(saved_filename):
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], saved_filename)
    if not os.path.exists(file_path):
        return jsonify({'error': '文件不存在'}), 404
    
    file_record = File.query.filter_by(saved_filename=saved_filename).first()
    original_filename = file_record.original_filename if file_record else saved_filename
    encoded_filename = quote(original_filename, encoding='utf-8')
    
    return send_file(
        file_path,
        as_attachment=True,
        download_name=encoded_filename,
        mimetype='application/octet-stream'
    )

# 删除文件
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

# 标签管理接口
@app.route('/tags', methods=['POST'])
def create_tag():
    try:
        tag_name = request.json.get('name').strip()
        if not tag_name:
            return jsonify({'error': '标签名称不能为空'}), 400
        
        existing_tag = Tag.query.filter_by(name=tag_name).first()
        if existing_tag:
            return jsonify({'error': '标签已存在'}), 400
        
        new_tag = Tag(name=tag_name)
        db.session.add(new_tag)
        db.session.commit()
        return jsonify({'success': True, 'tag': {'id': new_tag.id, 'name': new_tag.name}})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/tags', methods=['GET'])
def get_tags():
    tags = Tag.query.all()
    return jsonify([{'id': tag.id, 'name': tag.name} for tag in tags])

# 添加标签到文件
@app.route('/file/<file_id>/tags', methods=['POST'])
def add_tags_to_file(file_id):
    try:
        tag_ids = request.json.get('tag_ids', [])
        
        # 尝试先按file_id查询，如果失败再尝试按id查询
        file = File.query.filter_by(file_id=file_id).first()
        if not file:
            try:
                # 如果是数字ID，尝试按主键id查询
                file_id_int = int(file_id)
                file = File.query.get(file_id_int)
            except ValueError:
                pass
        
        if not file:
            return jsonify({'error': '文件不存在'}), 404
        
        tags = Tag.query.filter(Tag.id.in_(tag_ids)).all()
        file.tags = tags
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# 删除标签
@app.route('/tags/<int:tag_id>', methods=['DELETE'])
def delete_tag(tag_id):
    try:
        tag = Tag.query.get_or_404(tag_id)
        
        # 先移除该标签与所有文件的关联
        for file in tag.files:
            file.tags.remove(tag)
        
        # 然后删除标签
        db.session.delete(tag)
        db.session.commit()
        
        return jsonify({'success': True, 'message': f'标签 "{tag.name}" 已成功删除'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# 重命名文件
@app.route('/file/<int:file_id>/rename', methods=['POST'])
def rename_file(file_id):
    try:
        new_name = request.json.get('new_name').strip()
        if not new_name:
            return jsonify({'error': '文件名不能为空'}), 400
        
        file = File.query.get_or_404(file_id)
        file.original_filename = new_name
        db.session.commit()
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    with app.app_context():
        db.create_all()  # 创建数据库表
    app.run(debug=False, host="0.0.0.0", port=50001)
