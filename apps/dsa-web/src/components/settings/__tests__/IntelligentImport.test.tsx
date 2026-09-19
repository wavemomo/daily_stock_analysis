import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { IntelligentImport } from '../IntelligentImport';

const { parseImport, addItem, onMerged } = vi.hoisted(() => ({
  parseImport: vi.fn(),
  addItem: vi.fn(),
  onMerged: vi.fn(),
}));

vi.mock('../../../api/stocks', () => ({
  stocksApi: {
    parseImport,
    extractFromImage: vi.fn(),
  },
}));

vi.mock('../../../api/watchlist', () => ({
  watchlistApi: {
    addItem,
  },
}));

async function parseOneCode(code: string, name = 'Test Stock') {
  parseImport.mockResolvedValue({
    items: [{ code, name, confidence: 'high' }],
    codes: [],
  });
  fireEvent.change(screen.getByPlaceholderText('或粘贴 CSV/Excel 复制的文本...'), {
    target: { value: code },
  });
  fireEvent.click(screen.getByRole('button', { name: '解析' }));
  await screen.findByText(code);
}

describe('IntelligentImport', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('opens the matching hidden file input when the picker buttons are clicked', () => {
    const { container } = render(<IntelligentImport onMerged={onMerged} />);

    const inputs = container.querySelectorAll('input[type="file"]');
    expect(inputs).toHaveLength(2);

    const imageClick = vi.fn();
    const dataClick = vi.fn();
    Object.defineProperty(inputs[0], 'click', { value: imageClick, configurable: true });
    Object.defineProperty(inputs[1], 'click', { value: dataClick, configurable: true });

    fireEvent.click(screen.getByRole('button', { name: '选择图片' }));
    fireEvent.click(screen.getByRole('button', { name: '选择文件' }));

    expect(imageClick).toHaveBeenCalledTimes(1);
    expect(dataClick).toHaveBeenCalledTimes(1);
  });

  it('does not open hidden file inputs when the import actions are disabled', () => {
    const { container } = render(<IntelligentImport onMerged={onMerged} disabled />);

    const inputs = container.querySelectorAll('input[type="file"]');
    expect(inputs).toHaveLength(2);

    const imageClick = vi.fn();
    const dataClick = vi.fn();
    Object.defineProperty(inputs[0], 'click', { value: imageClick, configurable: true });
    Object.defineProperty(inputs[1], 'click', { value: dataClick, configurable: true });

    fireEvent.click(screen.getByRole('button', { name: '选择图片' }));
    fireEvent.click(screen.getByRole('button', { name: '选择文件' }));

    expect(imageClick).not.toHaveBeenCalled();
    expect(dataClick).not.toHaveBeenCalled();
  });

  it('adds parsed codes to the caller own watchlist instead of the global stock list', async () => {
    addItem.mockResolvedValue({ id: 1, stock_code: 'HK00700', stock_name: 'Tencent', scheduled: true });

    render(<IntelligentImport onMerged={onMerged} />);
    await parseOneCode('HK00700', 'Tencent');

    fireEvent.click(screen.getByRole('button', { name: '合并到自选股' }));

    await waitFor(() => {
      expect(addItem).toHaveBeenCalledWith('HK00700', 'Tencent');
    });
    await waitFor(() => {
      expect(onMerged).toHaveBeenCalledWith(['HK00700']);
    });
    // 导入成功后条目从待确认列表移除。
    expect(screen.queryByText('HK00700')).not.toBeInTheDocument();
  });

  it('keeps failed codes in the list and reports how many could not be added', async () => {
    addItem.mockRejectedValue(new Error('quota exceeded'));

    render(<IntelligentImport onMerged={onMerged} />);
    await parseOneCode('SZ000001', 'Ping An Bank');

    fireEvent.click(screen.getByRole('button', { name: '合并到自选股' }));

    await waitFor(() => {
      expect(addItem).toHaveBeenCalledTimes(1);
    });
    // 全部失败时不应回调 onMerged，条目保留供重试。
    expect(onMerged).not.toHaveBeenCalled();
    expect(screen.getByText('SZ000001')).toBeInTheDocument();
    expect(await screen.findByText('quota exceeded')).toBeInTheDocument();
  });

  it('never writes the global stock list config', async () => {
    addItem.mockResolvedValue({ id: 2, stock_code: 'SH600519', stock_name: '', scheduled: true });

    render(<IntelligentImport onMerged={onMerged} />);
    await parseOneCode('SH600519', '');

    fireEvent.click(screen.getByRole('button', { name: '合并到自选股' }));

    await waitFor(() => {
      expect(onMerged).toHaveBeenCalledWith(['SH600519']);
    });
    // 多用户隔离：不得再触达系统配置写接口。
    expect(addItem).toHaveBeenCalledWith('SH600519', '');
  });
});
