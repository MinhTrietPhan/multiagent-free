from pathlib import Path
from hub.agents.context.config import output_filename
from hub.agents.context.graph import build_context
MENU = '\nChọn mức ngữ cảnh:\n  1. map       Bản đồ nhanh (cây thư mục + skeleton)\n  2. balanced  Cân bằng (bản đồ + full code vài file chính)\n  3. full      Đầy đủ (toàn bộ code, đã bỏ comment/output notebook)\n'


def main():
    print('=== PROJECT CONTEXT GENERATOR ===')
    root = input('Đường dẫn dự án [.] : ').strip() or '.'
    project_root = Path(root).resolve()
    if not project_root.is_dir():
        print(f'Không tìm thấy thư mục: {project_root}')
        return
    print(f'\nDự án: {project_root}')
    print(MENU)
    choice = input('Chọn [2]: ').strip() or '2'
    mode = {'1': 'map', '2': 'balanced', '3': 'full'}.get(choice)
    if mode is None:
        print('Lựa chọn không hợp lệ.')
        return
    print(f'\nĐang tạo context ({mode})...')
    context = build_context(root=str(project_root), mode=mode)
    output_path = project_root / output_filename(mode)
    output_path.write_text(context, encoding='utf-8')
    print('\nĐã tạo context thành công!')
    print(f'File: {output_path}')
    print(f'Dung lượng: {len(context):,} ký tự')
if __name__ == '__main__':
    main()
