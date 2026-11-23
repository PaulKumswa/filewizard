// FileWizard native context-menu handler (IShellExtInit + IContextMenu).
//
// Why this exists: Windows caps registry-defined cascade menus at 16 entries (measured in CI),
// and static verbs can't see the selection. This handler builds the whole FileWizard ▸ tree
// itself, so it can show every action and hide multi-file actions ("Merge selected PDFs…")
// unless several files are selected.
//
// The DLL is deliberately dumb: the menu comes from a manifest that `filewizard install`
// writes to %LOCALAPPDATA%\FileWizard\menu.tsv, so adding actions never needs a rebuild.
// One click launches ONE process with every selected file (via a temp list file).
//
// Safety rules (this code runs inside explorer.exe): no exceptions escape, every index is
// bounds-checked, and if anything looks wrong we simply add no menu.

#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <shlobj.h>
#include <shlwapi.h>
#include <shellapi.h>
#include <new>
#include <string>
#include <vector>
#include <unordered_map>
#include <unordered_set>
#include <algorithm>

// {6E2B7D59-3D4A-4C61-9F0E-FA11E3A1D0C8}
static const CLSID CLSID_FileWizardMenu = {0x6e2b7d59, 0x3d4a, 0x4c61, {0x9f, 0x0e, 0xfa, 0x11, 0xe3, 0xa1, 0xd0, 0xc8}};

static LONG g_refs = 0;
static HINSTANCE g_module = nullptr;

// ---------------------------------------------------------------- manifest

struct Node {
    enum Kind { Item, Menu, Separator } kind = Item;
    std::wstring id;      // op id (items)
    std::wstring label;
    std::wstring mixedLabel;  // label when the selection mixes file types (optional)
    std::wstring icon;        // .ico path for the menu image (optional)
    bool multi = false;   // needs 2+ files
    bool mixed = false;   // only when the selection mixes file types (e.g. a video + an audio file)
    std::vector<Node> children;
};

struct Manifest {
    std::wstring command;   // e.g. "C:\...\filewizardw.exe" run --native
    std::wstring icon;
    std::unordered_map<std::wstring, std::vector<Node>> types;  // ".pdf" -> top-level nodes
};

static std::wstring Utf8ToWide(const std::string& s) {
    if (s.empty()) return std::wstring();
    int n = MultiByteToWideChar(CP_UTF8, 0, s.data(), (int)s.size(), nullptr, 0);
    if (n <= 0) return std::wstring();
    std::wstring w(n, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.data(), (int)s.size(), &w[0], n);
    return w;
}

static std::wstring AppDir() {
    PWSTR p = nullptr;
    std::wstring dir;
    if (SUCCEEDED(SHGetKnownFolderPath(FOLDERID_LocalAppData, 0, nullptr, &p)) && p) {
        dir = std::wstring(p) + L"\\FileWizard";
    }
    if (p) CoTaskMemFree(p);
    return dir;
}

// Opt-in diagnostics: create %LOCALAPPDATA%\FileWizard\shell-debug to get shell.log.
static void Log(const std::wstring& msg) {
    std::wstring dir = AppDir();
    if (dir.empty() || GetFileAttributesW((dir + L"\\shell-debug").c_str()) == INVALID_FILE_ATTRIBUTES) return;
    HANDLE f = CreateFileW((dir + L"\\shell.log").c_str(), FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE,
                           nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (f == INVALID_HANDLE_VALUE) return;
    std::wstring line = msg + L"\r\n";
    int n = WideCharToMultiByte(CP_UTF8, 0, line.c_str(), (int)line.size(), nullptr, 0, nullptr, nullptr);
    std::string u(n > 0 ? n : 0, '\0');
    if (n > 0) WideCharToMultiByte(CP_UTF8, 0, line.c_str(), (int)line.size(), &u[0], n, nullptr, nullptr);
    DWORD w = 0;
    WriteFile(f, u.data(), (DWORD)u.size(), &w, nullptr);
    CloseHandle(f);
}

static std::vector<std::wstring> SplitTabs(const std::wstring& line) {
    std::vector<std::wstring> out;
    size_t start = 0;
    while (true) {
        size_t t = line.find(L'\t', start);
        out.push_back(line.substr(start, t == std::wstring::npos ? std::wstring::npos : t - start));
        if (t == std::wstring::npos) break;
        start = t + 1;
    }
    return out;
}

// Manifest format (UTF-8, tab separated):
//   command <TAB> "...\filewizardw.exe" run --native
//   icon    <TAB> C:\...\filewizard.ico
//   type    <TAB> .pdf [<TAB> .pdfx ...]   (block shared by several extensions)
//   menu    <TAB> Convert to
//   menu    <TAB> Label [<TAB> icon.ico]
//   item    <TAB> op-id <TAB> Label <TAB> flags [<TAB> label for mixed selections [<TAB> icon.ico]]
//           (flags: m = needs 2+ files, x = needs a selection that mixes file types)
//   sep
//   end                                            (closes menu)
//   endtype
static std::wstring ModuleDir() {
    wchar_t buf[MAX_PATH * 4];
    DWORD n = GetModuleFileNameW(g_module, buf, (DWORD)(sizeof(buf) / sizeof(buf[0])));
    if (n == 0 || n >= sizeof(buf) / sizeof(buf[0])) return std::wstring();
    std::wstring p(buf, n);
    size_t slash = p.find_last_of(L"\\/");
    return slash == std::wstring::npos ? std::wstring() : p.substr(0, slash);
}

static bool LoadManifest(Manifest& m) {
    // all-users install: the manifest sits next to the DLL; per-user: in %LOCALAPPDATA%\FileWizard
    std::wstring path = ModuleDir() + L"\\menu.tsv";
    if (GetFileAttributesW(path.c_str()) == INVALID_FILE_ATTRIBUTES) path = AppDir() + L"\\menu.tsv";
    HANDLE f = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_DELETE, nullptr,
                           OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (f == INVALID_HANDLE_VALUE) return false;
    LARGE_INTEGER size{};
    if (!GetFileSizeEx(f, &size) || size.QuadPart <= 0 || size.QuadPart > 16 * 1024 * 1024) {
        CloseHandle(f);
        return false;
    }
    std::string data((size_t)size.QuadPart, '\0');
    DWORD read = 0;
    BOOL ok = ReadFile(f, &data[0], (DWORD)data.size(), &read, nullptr);
    CloseHandle(f);
    if (!ok || read != data.size()) return false;
    std::wstring text = Utf8ToWide(data);

    std::vector<std::wstring> typeNames;
    std::vector<Node> top;
    std::vector<Node*> stack;  // open submenus
    size_t pos = 0;
    while (pos < text.size()) {
        size_t nl = text.find(L'\n', pos);
        std::wstring line = text.substr(pos, nl == std::wstring::npos ? std::wstring::npos : nl - pos);
        pos = (nl == std::wstring::npos) ? text.size() : nl + 1;
        if (!line.empty() && line.back() == L'\r') line.pop_back();
        if (line.empty()) continue;
        auto f2 = SplitTabs(line);
        const std::wstring& k = f2[0];
        std::vector<Node>& target = stack.empty() ? top : stack.back()->children;
        if (k == L"command" && f2.size() >= 2) {
            m.command = f2[1];
        } else if (k == L"icon" && f2.size() >= 2) {
            m.icon = f2[1];
        } else if (k == L"type") {
            typeNames.assign(f2.begin() + 1, f2.end());
            top.clear();
            stack.clear();
        } else if (k == L"menu" && f2.size() >= 2) {
            Node n;
            n.kind = Node::Menu;
            n.label = f2[1];
            if (f2.size() >= 3) n.icon = f2[2];
            target.push_back(n);
            stack.push_back(&target.back());
        } else if (k == L"end") {
            if (!stack.empty()) stack.pop_back();
        } else if (k == L"sep") {
            Node n;
            n.kind = Node::Separator;
            target.push_back(n);
        } else if (k == L"item" && f2.size() >= 3) {
            Node n;
            n.kind = Node::Item;
            n.id = f2[1];
            n.label = f2[2];
            n.multi = f2.size() >= 4 && f2[3].find(L'm') != std::wstring::npos;
            n.mixed = f2.size() >= 4 && f2[3].find(L'x') != std::wstring::npos;
            if (f2.size() >= 5) n.mixedLabel = f2[4];
            if (f2.size() >= 6) n.icon = f2[5];
            target.push_back(n);
        } else if (k == L"endtype") {
            for (auto& t : typeNames) m.types[t] = top;
            typeNames.clear();
            top.clear();
            stack.clear();
        }
    }
    return !m.command.empty() && !m.types.empty();
}

// Recently used action ids (most recent first), written by FileWizard after each job.
static std::vector<std::wstring> LoadRecent() {
    std::vector<std::wstring> ids;
    HANDLE f = CreateFileW((AppDir() + L"\\recent.txt").c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_DELETE,
                           nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (f == INVALID_HANDLE_VALUE) return ids;
    char buf[4096];
    DWORD n = 0;
    BOOL ok = ReadFile(f, buf, sizeof(buf) - 1, &n, nullptr);
    CloseHandle(f);
    if (!ok) return ids;
    std::wstring text = Utf8ToWide(std::string(buf, n));
    size_t pos = 0;
    while (pos < text.size() && ids.size() < 10) {
        size_t nl = text.find(L'\n', pos);
        std::wstring line = text.substr(pos, nl == std::wstring::npos ? std::wstring::npos : nl - pos);
        pos = nl == std::wstring::npos ? text.size() : nl + 1;
        while (!line.empty() && (line.back() == L'\r' || line.back() == L' ')) line.pop_back();
        if (!line.empty()) ids.push_back(line);
    }
    return ids;
}

// Find an item by id anywhere in the tree; `path` collects the submenu names leading to it.
static const Node* FindItem(const std::vector<Node>& nodes, const std::wstring& id, std::wstring& path) {
    for (auto& n : nodes) {
        if (n.kind == Node::Item && n.id == id) return &n;
        if (n.kind == Node::Menu) {
            const Node* hit = FindItem(n.children, id, path);
            if (hit) {
                path = n.label + L" \u203A " + path;
                return hit;
            }
        }
    }
    return nullptr;
}

// ---------------------------------------------------------------- helpers

static void CollectIds(const std::vector<Node>& nodes, std::unordered_set<std::wstring>& out) {
    for (auto& n : nodes) {
        if (n.kind == Node::Item) out.insert(n.id);
        else if (n.kind == Node::Menu) CollectIds(n.children, out);
    }
}

// Keep items that every selected type supports; drop multi-file items for single selections;
// then tidy up empty submenus and doubled/edge separators.
static std::vector<Node> Filter(const std::vector<Node>& nodes, const std::vector<std::unordered_set<std::wstring>>& others,
                                size_t count) {
    std::vector<Node> out;
    for (auto& n : nodes) {
        if (n.kind == Node::Item) {
            if (n.multi && count < 2) continue;
            if (n.mixed && others.empty()) continue;
            bool everywhere = true;
            for (auto& s : others)
                if (!s.count(n.id)) { everywhere = false; break; }
            if (everywhere) {
                Node copy = n;
                if (!others.empty() && !n.mixedLabel.empty()) copy.label = n.mixedLabel;
                out.push_back(copy);
            }
        } else if (n.kind == Node::Menu) {
            Node copy = n;
            copy.children = Filter(n.children, others, count);
            bool any = false;
            for (auto& c : copy.children) if (c.kind != Node::Separator) { any = true; break; }
            if (any) out.push_back(copy);
        } else {
            out.push_back(n);
        }
    }
    std::vector<Node> tidy;
    for (auto& n : out) {
        if (n.kind == Node::Separator && (tidy.empty() || tidy.back().kind == Node::Separator)) continue;
        tidy.push_back(n);
    }
    while (!tidy.empty() && tidy.back().kind == Node::Separator) tidy.pop_back();
    return tidy;
}

// Menu key for a path: its lower-case extension, or "<folder>". Touching the disk is slow on
// network drives with big selections, so only names without a known extension are checked.
static std::wstring ExtKey(const std::wstring& path, const Manifest& m) {
    LPCWSTR ext = PathFindExtensionW(path.c_str());
    std::wstring e = (ext && *ext) ? ext : L"";
    std::transform(e.begin(), e.end(), e.begin(), ::towlower);
    if (!e.empty() && m.types.count(e)) return e;
    DWORD attrs = GetFileAttributesW(path.c_str());
    if (attrs != INVALID_FILE_ATTRIBUTES && (attrs & FILE_ATTRIBUTE_DIRECTORY)) return L"<folder>";
    return e;
}

// Menus treat '&' as a keyboard-shortcut marker; show a literal ampersand.
static std::wstring MenuText(const std::wstring& s) {
    std::wstring out;
    for (wchar_t c : s) {
        if (c == L'&') out += L'&';
        out += c;
    }
    return out;
}

// 32-bit premultiplied bitmap from an .ico, for the menu item image.
static HBITMAP IconToBitmap(const std::wstring& icoPath) {
    if (icoPath.empty()) return nullptr;
    int cx = GetSystemMetrics(SM_CXSMICON), cy = GetSystemMetrics(SM_CYSMICON);
    HICON icon = (HICON)LoadImageW(nullptr, icoPath.c_str(), IMAGE_ICON, cx, cy, LR_LOADFROMFILE);
    if (!icon) return nullptr;
    BITMAPINFO bmi{};
    bmi.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    bmi.bmiHeader.biWidth = cx;
    bmi.bmiHeader.biHeight = -cy;
    bmi.bmiHeader.biPlanes = 1;
    bmi.bmiHeader.biBitCount = 32;
    bmi.bmiHeader.biCompression = BI_RGB;
    void* bits = nullptr;
    HDC screen = GetDC(nullptr);
    HBITMAP bmp = CreateDIBSection(screen, &bmi, DIB_RGB_COLORS, &bits, nullptr, 0);
    if (bmp) {
        HDC dc = CreateCompatibleDC(screen);
        HGDIOBJ old = SelectObject(dc, bmp);
        ZeroMemory(bits, (size_t)cx * cy * 4);
        DrawIconEx(dc, 0, 0, icon, cx, cy, 0, nullptr, DI_NORMAL);
        SelectObject(dc, old);
        DeleteDC(dc);
    }
    ReleaseDC(nullptr, screen);
    DestroyIcon(icon);
    return bmp;
}

// ---------------------------------------------------------------- the handler

class FileWizardMenu : public IShellExtInit, public IContextMenu {
public:
    FileWizardMenu() { InterlockedIncrement(&g_refs); }
    virtual ~FileWizardMenu() {
        if (m_bitmap) DeleteObject(m_bitmap);
        for (HBITMAP b : m_itemBitmaps) DeleteObject(b);
        InterlockedDecrement(&g_refs);
    }

    // IUnknown
    IFACEMETHODIMP QueryInterface(REFIID riid, void** ppv) override {
        if (!ppv) return E_POINTER;
        *ppv = nullptr;
        if (riid == IID_IUnknown || riid == IID_IContextMenu) *ppv = static_cast<IContextMenu*>(this);
        else if (riid == IID_IShellExtInit) *ppv = static_cast<IShellExtInit*>(this);
        else return E_NOINTERFACE;
        AddRef();
        return S_OK;
    }
    IFACEMETHODIMP_(ULONG) AddRef() override { return InterlockedIncrement(&m_ref); }
    IFACEMETHODIMP_(ULONG) Release() override {
        ULONG r = InterlockedDecrement(&m_ref);
        if (r == 0) delete this;
        return r;
    }

    // IShellExtInit: remember the selection
    IFACEMETHODIMP Initialize(PCIDLIST_ABSOLUTE, IDataObject* data, HKEY) override {
        m_files.clear();
        if (!data) return E_INVALIDARG;
        FORMATETC fe = {CF_HDROP, nullptr, DVASPECT_CONTENT, -1, TYMED_HGLOBAL};
        STGMEDIUM stm{};
        if (FAILED(data->GetData(&fe, &stm))) return E_INVALIDARG;
        Log(L"Initialize: got CF_HDROP");
        HDROP drop = (HDROP)GlobalLock(stm.hGlobal);
        if (drop) {
            UINT n = DragQueryFileW(drop, 0xFFFFFFFF, nullptr, 0);
            for (UINT i = 0; i < n && i < 100000; ++i) {
                UINT len = DragQueryFileW(drop, i, nullptr, 0);
                std::wstring p(len + 1, L'\0');
                DragQueryFileW(drop, i, &p[0], len + 1);
                p.resize(len);
                if (!p.empty()) m_files.push_back(p);
            }
            GlobalUnlock(stm.hGlobal);
        }
        ReleaseStgMedium(&stm);
        Log(L"Initialize: files=" + std::to_wstring(m_files.size()));
        return m_files.empty() ? E_INVALIDARG : S_OK;
    }

    // IContextMenu
    IFACEMETHODIMP QueryContextMenu(HMENU menu, UINT index, UINT first, UINT last, UINT flags) override {
        try {
            if (flags & CMF_DEFAULTONLY) return MAKE_HRESULT(SEVERITY_SUCCESS, 0, 0);
            Log(L"QueryContextMenu: flags=" + std::to_wstring(flags) + L" files=" + std::to_wstring(m_files.size()));
            Manifest m;
            if (!LoadManifest(m)) {
                Log(L"QueryContextMenu: manifest missing/invalid at " + AppDir() + L"\\menu.tsv");
                return MAKE_HRESULT(SEVERITY_SUCCESS, 0, 0);
            }
            Log(L"QueryContextMenu: manifest types=" + std::to_wstring(m.types.size()));
            m_command = m.command;

            // which menu: the first file's type; others narrow it to shared actions
            std::vector<std::wstring> keys;
            for (auto& f : m_files) keys.push_back(ExtKey(f, m));
            auto pick = [&](const std::wstring& k) -> const std::vector<Node>* {
                auto it = m.types.find(k);
                if (it == m.types.end() && k != L"<folder>") it = m.types.find(L"*");
                return it == m.types.end() ? nullptr : &it->second;
            };
            const std::vector<Node>* base = pick(keys.empty() ? std::wstring() : keys[0]);
            Log(L"QueryContextMenu: first key=" + (keys.empty() ? std::wstring() : keys[0]) + (base ? L" found" : L" NOT found"));
            if (!base) return MAKE_HRESULT(SEVERITY_SUCCESS, 0, 0);
            std::vector<std::unordered_set<std::wstring>> others;
            std::unordered_set<std::wstring> seen{keys[0]};
            for (auto& k : keys) {
                if (seen.count(k)) continue;
                seen.insert(k);
                const std::vector<Node>* t = pick(k);
                std::unordered_set<std::wstring> ids;
                if (t) CollectIds(*t, ids);
                others.push_back(ids);
            }
            std::vector<Node> nodes = Filter(*base, others, m_files.size());
            if (nodes.empty()) return MAKE_HRESULT(SEVERITY_SUCCESS, 0, 0);

            // up to 3 recently used actions that apply here, on top for one-hover access
            std::vector<Node> recent;
            for (auto& id : LoadRecent()) {
                if (recent.size() >= 3 || id == L"palette") continue;
                std::wstring path;
                const Node* hit = FindItem(nodes, id, path);
                if (!hit || path.empty()) continue;  // top-level items are already one hover away
                Node copy = *hit;
                copy.label = path + copy.label;
                recent.push_back(copy);
            }
            if (!recent.empty()) {
                Node sep;
                sep.kind = Node::Separator;
                recent.push_back(sep);
                nodes.insert(nodes.begin(), recent.begin(), recent.end());
            }

            m_ids.clear();
            HMENU sub = Build(nodes, first, last);
            if (!sub) return MAKE_HRESULT(SEVERITY_SUCCESS, 0, 0);

            MENUITEMINFOW mii{sizeof(mii)};
            mii.fMask = MIIM_SUBMENU | MIIM_STRING | MIIM_ID;
            mii.wID = first + (UINT)m_ids.size();  // the popup itself gets an (unused) id too
            mii.hSubMenu = sub;
            mii.dwTypeData = const_cast<LPWSTR>(L"FileWizard");
            if (!m_bitmap) m_bitmap = IconToBitmap(m.icon);
            if (m_bitmap) {
                mii.fMask |= MIIM_BITMAP;
                mii.hbmpItem = m_bitmap;
            }
            Log(L"QueryContextMenu: inserting, ids=" + std::to_wstring(m_ids.size()));
            if (!InsertMenuItemW(menu, index, TRUE, &mii)) {
                Log(L"QueryContextMenu: InsertMenuItemW failed " + std::to_wstring(GetLastError()));
                DestroyMenu(sub);
                return MAKE_HRESULT(SEVERITY_SUCCESS, 0, 0);
            }
            return MAKE_HRESULT(SEVERITY_SUCCESS, 0, (USHORT)(m_ids.size() + 1));
        } catch (...) {
            return MAKE_HRESULT(SEVERITY_SUCCESS, 0, 0);
        }
    }

    IFACEMETHODIMP InvokeCommand(LPCMINVOKECOMMANDINFO info) override {
        try {
            if (!info || HIWORD(info->lpVerb) != 0) return E_FAIL;  // we only use numeric ids
            UINT offset = LOWORD(info->lpVerb);
            if (offset >= m_ids.size() || m_command.empty()) return E_FAIL;
            return Launch(m_ids[offset]) ? S_OK : E_FAIL;
        } catch (...) {
            return E_FAIL;
        }
    }

    IFACEMETHODIMP GetCommandString(UINT_PTR idCmd, UINT type, UINT*, LPSTR name, UINT cch) override {
        if (type == GCS_VERBW && idCmd < m_ids.size() && name && cch > 0) {
            std::wstring verb = L"filewizard." + m_ids[idCmd];
            lstrcpynW(reinterpret_cast<LPWSTR>(name), verb.c_str(), (int)cch);
            return S_OK;
        }
        return E_NOTIMPL;
    }

private:
    HMENU Build(const std::vector<Node>& nodes, UINT first, UINT last) {
        HMENU h = CreatePopupMenu();
        if (!h) return nullptr;
        for (auto& n : nodes) {
            if (n.kind == Node::Separator) {
                AppendMenuW(h, MF_SEPARATOR, 0, nullptr);
            } else if (n.kind == Node::Menu) {
                HMENU child = Build(n.children, first, last);
                if (child) {
                    AppendMenuW(h, MF_POPUP | MF_STRING, (UINT_PTR)child, MenuText(n.label).c_str());
                    SetImage(h, n.icon);
                }
            } else {
                UINT id = first + (UINT)m_ids.size();
                if (id >= last) break;  // out of id space: stop adding
                m_ids.push_back(n.id);
                AppendMenuW(h, MF_STRING, id, MenuText(n.label).c_str());
                SetImage(h, n.icon);
            }
        }
        return h;
    }

    static std::wstring Quote(const std::wstring& s) {
        // Windows command-line quoting: backslashes before a quote must be doubled
        std::wstring out = L"\"";
        size_t bs = 0;
        for (wchar_t c : s) {
            if (c == L'\\') { ++bs; continue; }
            if (c == L'"') { out.append(bs * 2 + 1, L'\\'); out += L'"'; bs = 0; continue; }
            out.append(bs, L'\\');
            bs = 0;
            out += c;
        }
        out.append(bs * 2, L'\\');
        out += L'"';
        return out;
    }

    bool Launch(const std::wstring& opId) {
        std::wstring cmd = m_command + L" " + opId;
        // few files: pass them directly; many: hand over a UTF-8 list file (command lines max out at 32K)
        size_t total = cmd.size();
        for (auto& f : m_files) total += f.size() + 3;
        if (total < 30000) {
            for (auto& f : m_files) cmd += L" " + Quote(f);
        } else {
            std::wstring dir = AppDir();
            CreateDirectoryW(dir.c_str(), nullptr);
            wchar_t name[MAX_PATH];
            if (!GetTempFileNameW(dir.c_str(), L"sel", 0, name)) return false;
            HANDLE f = CreateFileW(name, GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_TEMPORARY, nullptr);
            if (f == INVALID_HANDLE_VALUE) return false;
            for (auto& p : m_files) {
                std::wstring line = p + L"\n";
                int n = WideCharToMultiByte(CP_UTF8, 0, line.c_str(), (int)line.size(), nullptr, 0, nullptr, nullptr);
                std::string u(n, '\0');
                WideCharToMultiByte(CP_UTF8, 0, line.c_str(), (int)line.size(), &u[0], n, nullptr, nullptr);
                DWORD w = 0;
                WriteFile(f, u.data(), (DWORD)u.size(), &w, nullptr);
            }
            CloseHandle(f);
            cmd += L" --list " + Quote(name);
        }
        std::wstring workdir = m_files[0];
        PathRemoveFileSpecW(&workdir[0]);
        workdir.resize(wcslen(workdir.c_str()));

        STARTUPINFOW si{sizeof(si)};
        PROCESS_INFORMATION pi{};
        std::vector<wchar_t> buf(cmd.begin(), cmd.end());
        buf.push_back(L'\0');
        BOOL ok = CreateProcessW(nullptr, buf.data(), nullptr, nullptr, FALSE, CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT,
                                 nullptr, workdir.empty() ? nullptr : workdir.c_str(), &si, &pi);
        if (ok) {
            AllowSetForegroundWindow(pi.dwProcessId);  // let its dialogs come to the front
            CloseHandle(pi.hThread);
            CloseHandle(pi.hProcess);
        }
        return ok != FALSE;
    }

    LONG m_ref = 1;
    std::vector<std::wstring> m_files;
    std::vector<std::wstring> m_ids;  // menu id offset -> op id
    std::wstring m_command;
    HBITMAP m_bitmap = nullptr;
    std::vector<HBITMAP> m_itemBitmaps;  // menu images; must live as long as the menu
    std::unordered_map<std::wstring, HBITMAP> m_iconCache;

    void SetImage(HMENU h, const std::wstring& icon) {
        if (icon.empty()) return;
        HBITMAP bmp = nullptr;
        auto it = m_iconCache.find(icon);
        if (it != m_iconCache.end()) {
            bmp = it->second;
        } else {
            bmp = IconToBitmap(icon);
            m_iconCache[icon] = bmp;
            if (bmp) m_itemBitmaps.push_back(bmp);
        }
        if (!bmp) return;
        MENUITEMINFOW mii{sizeof(mii)};
        mii.fMask = MIIM_BITMAP;
        mii.hbmpItem = bmp;
        SetMenuItemInfoW(h, GetMenuItemCount(h) - 1, TRUE, &mii);  // the item just appended
    }
};

// ---------------------------------------------------------------- class factory & exports

class Factory : public IClassFactory {
public:
    IFACEMETHODIMP QueryInterface(REFIID riid, void** ppv) override {
        if (!ppv) return E_POINTER;
        if (riid == IID_IUnknown || riid == IID_IClassFactory) {
            *ppv = static_cast<IClassFactory*>(this);
            AddRef();
            return S_OK;
        }
        *ppv = nullptr;
        return E_NOINTERFACE;
    }
    IFACEMETHODIMP_(ULONG) AddRef() override { InterlockedIncrement(&g_refs); return 2; }
    IFACEMETHODIMP_(ULONG) Release() override { InterlockedDecrement(&g_refs); return 1; }
    IFACEMETHODIMP CreateInstance(IUnknown* outer, REFIID riid, void** ppv) override {
        if (outer) return CLASS_E_NOAGGREGATION;
        FileWizardMenu* obj = new (std::nothrow) FileWizardMenu();
        if (!obj) return E_OUTOFMEMORY;
        HRESULT hr = obj->QueryInterface(riid, ppv);
        obj->Release();
        return hr;
    }
    IFACEMETHODIMP LockServer(BOOL lock) override {
        if (lock) InterlockedIncrement(&g_refs); else InterlockedDecrement(&g_refs);
        return S_OK;
    }
};

static Factory g_factory;

STDAPI DllGetClassObject(REFCLSID clsid, REFIID riid, void** ppv) {
    Log(L"DllGetClassObject");
    if (clsid != CLSID_FileWizardMenu) return CLASS_E_CLASSNOTAVAILABLE;
    return g_factory.QueryInterface(riid, ppv);
}

STDAPI DllCanUnloadNow() { return g_refs == 0 ? S_OK : S_FALSE; }

BOOL APIENTRY DllMain(HINSTANCE inst, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        g_module = inst;
        DisableThreadLibraryCalls(inst);
    }
    return TRUE;
}
