using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;

// Pure deterministic donor composition. No files, UMT, system fonts or clock.
public static class FontAtlasCore
{
    public const string Recipe = "existing-pixels/v3";
    public static readonly string[] Names = { "Nerko", "NerkoLarge", "NerkoLarge2", "NerkoSmall" };
    public sealed class Bitmap
    {
        public readonly int W, H;
        public readonly byte[] Pixels;
        public Bitmap(int w, int h, byte[] pixels)
        {
            Need(w > 0 && h > 0 && w <= 4096 && h <= 4096, "BITMAP_DIMENSIONS");
            Need(pixels != null && pixels.Length == checked(w * h * 4), "BITMAP_LENGTH");
            W = w; H = h; Pixels = (byte[])pixels.Clone();
        }
        public bool Ink(int x, int y) => Pixels[(y * W + x) * 4 + 3] != 0;
    }
    public sealed class Glyph
    {
        public char Character;
        public Bitmap Image;
        public int Shift, Offset;
        public JsonElement Kerning;
    }
    public sealed class Composed
    {
        public Glyph Glyph;
        public object Provenance;
    }
    public static void Need(bool value, string code) => FontBuilderCore.Require(value, code);
    public static Bitmap FromBgra(int width, int height, byte[] bgra)
    {
        var result = new Bitmap(width, height, bgra); // Own copy; never swizzle the UMT-owned buffer.
        for (int i = 0; i < result.Pixels.Length; i += 4)
        { byte b = result.Pixels[i]; result.Pixels[i] = result.Pixels[i + 2]; result.Pixels[i + 2] = b; }
        return result;
    }
    public static (int x, int y, int w, int h) Bounds(Bitmap b)
    {
        int left = b.W, top = b.H, right = -1, bottom = -1;
        for (int y = 0; y < b.H; y++) for (int x = 0; x < b.W; x++) if (b.Ink(x, y))
        { left = Math.Min(left, x); top = Math.Min(top, y); right = Math.Max(right, x); bottom = Math.Max(bottom, y); }
        Need(right >= 0, "EMPTY_ALPHA");
        return (left, top, right - left + 1, bottom - top + 1);
    }
    public static Bitmap Crop(Bitmap b, int x, int y, int w, int h)
    {
        Need(x >= 0 && y >= 0 && w > 0 && h > 0 && x <= b.W - w && y <= b.H - h, "CROP_BOUNDS");
        var p = new byte[checked(w * h * 4)];
        for (int row = 0; row < h; row++) Array.Copy(b.Pixels, ((y + row) * b.W + x) * 4, p, row * w * 4, w * 4);
        return new Bitmap(w, h, p);
    }
    public static void Metrics(Glyph g)
    {
        Need(g != null && g.Image != null && g.Shift >= short.MinValue && g.Shift <= short.MaxValue &&
            g.Offset >= short.MinValue && g.Offset <= short.MaxValue, "METRICS_RANGE");
        Need(g.Kerning.ValueKind == JsonValueKind.Array && g.Kerning.GetArrayLength() <= 4096, "KERNING_ARRAY");
        var seen = new HashSet<int>();
        foreach (var k in g.Kerning.EnumerateArray())
        {
            FontBuilderCore.Fields(k, "character", "shiftModifier");
            Need(seen.Add(FontBuilderCore.Number(k, "character", short.MinValue, short.MaxValue)), "KERNING_DUPLICATE");
            FontBuilderCore.Number(k, "shiftModifier", short.MinValue, short.MaxValue);
        }
        Bounds(g.Image);
    }
    static void Connected(Bitmap b)
    {
        var seen = new HashSet<int>(); var queue = new Queue<int>(); int total = 0;
        for (int i = 0; i < b.W * b.H; i++) if (b.Pixels[i * 4 + 3] != 0)
        { total++; if (queue.Count == 0) queue.Enqueue(i); }
        Need(total > 0, "EMPTY_COMPONENT");
        seen.Add(queue.Peek());
        while (queue.Count > 0)
        {
            int i = queue.Dequeue(), x = i % b.W, y = i / b.W;
            for (int dy = -1; dy <= 1; dy++) for (int dx = -1; dx <= 1; dx++)
            {
                int xx = x + dx, yy = y + dy;
                if (xx >= 0 && yy >= 0 && xx < b.W && yy < b.H && b.Ink(xx, yy) && seen.Add(yy * b.W + xx))
                    queue.Enqueue(yy * b.W + xx);
            }
        }
        Need(seen.Count == total, "AMBIGUOUS_COMPONENT");
    }
    // Exactly two occupied row bands separated by >=1 empty row; upper is one 8-connected component.
    public static (Bitmap mark, int x, int y, int bodyY) Upper(Bitmap b)
    {
        Bounds(b); var bands = new List<(int first, int last)>(); int start = -1;
        for (int y = 0; y <= b.H; y++)
        {
            bool ink = y < b.H && Enumerable.Range(0, b.W).Any(x => b.Ink(x, y));
            if (ink && start < 0) start = y;
            if (!ink && start >= 0) { bands.Add((start, y - 1)); start = -1; }
        }
        Need(bands.Count == 2, "NO_UNAMBIGUOUS_UPPER_COMPONENT");
        var upper = Crop(b, 0, bands[0].first, b.W, bands[0].last - bands[0].first + 1);
        var ub = Bounds(upper); var mark = Crop(upper, ub.x, ub.y, ub.w, ub.h);
        Connected(mark);
        Need(mark.H * 2 <= bands[1].last - bands[1].first + 1, "MARK_TOO_LARGE");
        return (mark, ub.x, bands[0].first, bands[1].first);
    }
    static bool SamePixel(Bitmap a, int ax, int ay, Bitmap b, int bx, int by)
    {
        bool ai = ax >= 0 && ay >= 0 && ax < a.W && ay < a.H && a.Ink(ax, ay);
        bool bi = bx >= 0 && by >= 0 && bx < b.W && by < b.H && b.Ink(bx, by);
        if (ai != bi) return false;
        return !ai || Enumerable.Range(0, 4).All(c => a.Pixels[(ay * a.W + ax) * 4 + c] == b.Pixels[(by * b.W + bx) * 4 + c]);
    }
    // Alpha > 0, including faint AA; diagonal contact joins components.
    static List<List<int>> Components(Bitmap b)
    {
        var result = new List<List<int>>(); var seen = new bool[b.W * b.H];
        for (int seed = 0; seed < seen.Length; seed++)
        {
            if (seen[seed] || b.Pixels[seed * 4 + 3] == 0) continue;
            var component = new List<int>(); var queue = new Queue<int>();
            queue.Enqueue(seed); seen[seed] = true;
            while (queue.Count != 0)
            {
                int p = queue.Dequeue(); component.Add(p);
                for (int dy = -1; dy <= 1; dy++) for (int dx = -1; dx <= 1; dx++)
                {
                    int x = p % b.W + dx, y = p / b.W + dy;
                    if (x < 0 || y < 0 || x >= b.W || y >= b.H) continue;
                    int n = y * b.W + x;
                    if (!seen[n] && b.Ink(x, y)) { seen[n] = true; queue.Enqueue(n); }
                }
            }
            result.Add(component);
        }
        return result;
    }
    public static Bitmap Acute(Glyph accented, Glyph plain) => Acute(accented, plain, out _);
    public static Bitmap Acute(Glyph accented, Glyph plain, out object diagnostic)
    {
        Metrics(accented); Metrics(plain);
        Need((accented.Character == 'ó' && plain.Character == 'o') ||
            (accented.Character == 'Ó' && plain.Character == 'O'), "ACUTE_DONOR_PAIR");
        Need(Components(accented.Image).Count == 2, "ACUTE_COMPONENT_COUNT_OR_CONTACT");
        var u = Upper(accented.Image);
        var body = Crop(accented.Image, 0, u.bodyY, accented.Image.W, accented.Image.H - u.bodyY);
        var bb = Bounds(body);
        int area = u.mark.Pixels.Where((v, i) => i % 4 == 3 && v != 0).Count();
        int bodyArea = body.Pixels.Where((v, i) => i % 4 == 3 && v != 0).Count();
        Need(u.mark.W <= bb.w && u.mark.W <= 3 * u.mark.H && u.mark.H <= 3 * u.mark.W,
            "ACUTE_PROPORTIONS");
        Need(area * 3 <= bodyArea, "ACUTE_AREA_TOO_LARGE");
        int gap = u.bodyY - u.y - u.mark.H;
        Need(gap >= 1 && gap <= Math.Max(1, bb.h / 2) &&
            u.x >= bb.x - bb.w / 4 && u.x + u.mark.W <= bb.x + bb.w + bb.w / 4,
            "ACUTE_BAD_POSITION");
        bool bodyExact = true;
        // Compare body in rendering coordinates, not tightly cropped/vertically realigned coordinates.
        for (int y = 0; y < Math.Max(plain.Image.H, accented.Image.H); y++)
            for (int x = Math.Min(plain.Offset, accented.Offset); x < Math.Max(plain.Offset + plain.Image.W, accented.Offset + accented.Image.W); x++)
                bodyExact &= SamePixel(accented.Image, x - accented.Offset, y < u.bodyY ? -1 : y, plain.Image, x - plain.Offset, y);
        Need(u.mark.W >= 2 && u.mark.H >= 2, "ACUTE_TOO_SMALL");
        double top = Enumerable.Range(0, u.mark.W).Where(x => u.mark.Ink(x, 0)).Average();
        double bottom = Enumerable.Range(0, u.mark.W).Where(x => u.mark.Ink(x, u.mark.H - 1)).Average();
        Need(top > bottom, "ACUTE_NOT_RISING_RIGHT");
        diagnostic = new { donor = accented.Character.ToString(), components = 2, connectivity = 8,
            x = u.x, y = u.y, width = u.mark.W, height = u.mark.H, gap, area, bodyArea,
            bodyExact, advanceExact = accented.Shift == plain.Shift,
            warnings = new[] { bodyExact ? null : "ACUTE_BODY_NOT_EXACT_BASE",
                accented.Shift == plain.Shift ? null : "ACUTE_ADVANCE_MISMATCH" }.Where(s => s != null).ToArray() };
        return u.mark;
    }
    public static Bitmap Dot(Glyph donor, bool fromI)
    {
        Metrics(donor);
        Bitmap dot;
        if (fromI) dot = Upper(donor.Image).mark;
        else { var r = Bounds(donor.Image); dot = Crop(donor.Image, r.x, r.y, r.w, r.h); }
        Connected(dot);
        Need(dot.W <= dot.H * 2 && dot.H <= dot.W * 2, "DOT_NOT_COMPACT");
        return dot;
    }
    public static char Base(char c)
    {
        const string targets = "ąćęłńśźżĄĆĘŁŃŚŹŻ";
        const string bases = "acelnszzACELNSZZ";
        int i = targets.IndexOf(c); Need(i >= 0, "UNSUPPORTED_ADDITION"); return bases[i];
    }
    static double RowCenter(Bitmap b, int y)
    {
        long mass = 0, moment = 0;
        for (int x = 0; x < b.W; x++) { int a = b.Pixels[(y * b.W + x) * 4 + 3]; mass += a; moment += (long)a * x; }
        Need(mass > 0, "DONOR_EMPTY_ROW"); return (double)moment / mass;
    }
    // Exact polygon/box intersection area, not NN rotation, binary masks or repeated resize.
    // Source pixels are constant straight RGBA cells; accumulate coverage in premultiplied space.
    static Bitmap AreaTransform(Bitmap source, Func<double, double, (double x, double y)> map)
    {
        var cells = new List<((double x, double y)[] polygon, int pixel)>();
        for (int y = 0; y < source.H; y++) for (int x = 0; x < source.W; x++)
            cells.Add((new[] { map(x, y), map(x + 1, y), map(x + 1, y + 1), map(x, y + 1) }, y * source.W + x));
        int left = (int)Math.Floor(cells.Min(c => c.polygon.Min(p => p.x))), top = (int)Math.Floor(cells.Min(c => c.polygon.Min(p => p.y)));
        int w = (int)Math.Ceiling(cells.Max(c => c.polygon.Max(p => p.x))) - left;
        int h = (int)Math.Ceiling(cells.Max(c => c.polygon.Max(p => p.y))) - top;
        Need(w > 0 && h > 0 && w <= 512 && h <= 512, "TRANSFORM_BOUNDS");
        var sums = new double[w * h * 4];
        foreach (var cell in cells)
        {
            int si = cell.pixel * 4; if (source.Pixels[si + 3] == 0) continue;
            int x0 = Math.Max(left, (int)Math.Floor(cell.polygon.Min(p => p.x))), x1 = Math.Min(left + w, (int)Math.Ceiling(cell.polygon.Max(p => p.x)));
            int y0 = Math.Max(top, (int)Math.Floor(cell.polygon.Min(p => p.y))), y1 = Math.Min(top + h, (int)Math.Ceiling(cell.polygon.Max(p => p.y)));
            for (int y = y0; y < y1; y++) for (int x = x0; x < x1; x++)
            {
                var poly = cell.polygon.ToList();
                for (int edge = 0; edge < 4 && poly.Count > 0; edge++)
                {
                    bool horizontal = edge >= 2; double bound = edge == 0 ? x : edge == 1 ? x + 1 : edge == 2 ? y : y + 1;
                    Func<(double x, double y), double> distance = p => ((edge % 2 == 0) ? 1 : -1) * ((horizontal ? p.y : p.x) - bound);
                    var clipped = new List<(double x, double y)>(); var prev = poly[poly.Count - 1]; double dp = distance(prev);
                    foreach (var current in poly)
                    {
                        double dc = distance(current);
                        if ((dp >= 0) != (dc >= 0))
                        { double t = dp / (dp - dc); clipped.Add((prev.x + t * (current.x - prev.x), prev.y + t * (current.y - prev.y))); }
                        if (dc >= 0) clipped.Add(current);
                        prev = current; dp = dc;
                    }
                    poly = clipped;
                }
                double area = 0;
                for (int i = 0; i < poly.Count; i++) { var a = poly[i]; var b = poly[(i + 1) % poly.Count]; area += a.x * b.y - b.x * a.y; }
                double alpha = Math.Abs(area) * 0.5 * source.Pixels[si + 3]; int di = ((y - top) * w + x - left) * 4;
                sums[di + 3] += alpha;
                for (int c = 0; c < 3; c++) sums[di + c] += alpha * source.Pixels[si + c];
            }
        }
        var pixels = new byte[w * h * 4];
        for (int i = 0; i < pixels.Length; i += 4)
        {
            double a = sums[i + 3]; pixels[i + 3] = (byte)Math.Min(255, Math.Floor(a + 0.5));
            if (pixels[i + 3] == 0) continue;
            for (int c = 0; c < 3; c++) pixels[i + c] = (byte)Math.Clamp(Math.Floor(sums[i + c] / a + 0.5), 0, 255);
        }
        var result = new Bitmap(w, h, pixels); var r = Bounds(result);
        return Crop(result, r.x, r.y, r.w, r.h); // transparent-only trim: never discard AA > 0
    }
    public static void ValidateHook(Bitmap mark)
    {
        Connected(mark);
        Need(mark.H >= 3, "HOOK_TOO_SHORT");
        var centers = Enumerable.Range(0, mark.H).Select(y => RowCenter(mark, y)).ToArray();
        double minimum = centers.Min(); int turn = Array.IndexOf(centers, minimum);
        Need(turn > 0 && turn < mark.H - 1 && centers[0] > minimum + 0.25 && centers[mark.H - 1] > minimum + 0.25,
            "HOOK_Z_OR_NO_RETURN");
        // A z has broad horizontal top/bottom bars rather than a curved, tapered end.
        int topWidth = Enumerable.Range(0, mark.W).Count(x => mark.Ink(x, 0));
        int bottomWidth = Enumerable.Range(0, mark.W).Count(x => mark.Ink(x, mark.H - 1));
        Need(topWidth < mark.W && bottomWidth < mark.W, "HOOK_Z_BARS");
    }
    public static void ValidateStroke(Bitmap mark)
    {
        Connected(mark);
        Need(mark.W >= 3 && mark.H >= 3, "STROKE_NOT_DIAGONAL");
        double leftMass = 0, leftY = 0, rightMass = 0, rightY = 0;
        for (int y = 0; y < mark.H; y++) for (int x = 0; x < mark.W; x++)
        {
            int a = mark.Pixels[(y * mark.W + x) * 4 + 3];
            if (x < mark.W / 3) { leftMass += a; leftY += a * y; }
            if (x >= mark.W - mark.W / 3) { rightMass += a; rightY += a * y; }
        }
        Need(leftMass > 0 && rightMass > 0 && leftY / leftMass - rightY / rightMass >= 1, "STROKE_HORIZONTAL_TABS");
    }
    public static Composed Compose(char character, Glyph basis, Bitmap mark, string donor, int maxHeight, float emSize = 24)
    {
        Need(Base(character) == basis.Character, "WRONG_BASE"); Metrics(basis); Bounds(mark);
        Need(maxHeight >= basis.Image.H && maxHeight <= 512 && basis.Image.W <= 512, "FONT_ENVELOPE");
        var b = Bounds(basis.Image);
        var points = new List<(int x, int y, int pixel)>();
        string operation; object geometry; object donorTransform = null;
        bool above = "ćńśźżĆŃŚŹŻ".Contains(character);
        if (above)
        {
            operation = "copy-component-centered-gap1";
            int x0 = b.x + (b.w - mark.W) / 2, y0 = b.y - 1 - mark.H;
            Need(mark.W <= b.w && y0 >= 0, "NO_HEADROOM_WITHOUT_BASELINE_SHIFT");
            geometry = new { x = x0, y = y0, width = mark.W, height = mark.H, gap = 1 };
            for (int y = 0; y < mark.H; y++) for (int x = 0; x < mark.W; x++)
                if (mark.Ink(x, y)) points.Add((x0 + x, y0 + y, y * mark.W + x));
        }
        else
        {
            bool hook = "ąęĄĘ".Contains(character);
            Need(donor == (hook ? "," : "/"), "WRONG_PUNCTUATION_DONOR");
            Need(mark.W <= 512 && mark.H <= 512, "DONOR_DIMENSIONS");
            var source = mark; var crop = Bounds(source); var tight = Crop(source, crop.x, crop.y, crop.w, crop.h);
            Connected(tight); Need(tight.W >= 2 && tight.H >= 3, "DONOR_TOO_SMALL");
            int x0, y0; object transform;
            if ("ąęĄĘ".Contains(character))
            {
                operation = "comma-area-curved-hook-base-wins";
                // Piecewise-affine row translation: retain each donor row's RGBA/width profile,
                // bend its centre into a small hook. No stamped pixels or letter-z source.
                double[] centers = Enumerable.Range(0, tight.H).Select(y => RowCenter(tight, y)).ToArray();
                double[] shifts = Enumerable.Range(0, tight.H + 1).Select(y => {
                    double t = (double)y / tight.H;
                    double center = y == 0 ? centers[0] : y == tight.H ? centers[tight.H - 1] : (centers[y - 1] + centers[y]) / 2;
                    return tight.W * 0.40 * (t - 0.55) * (t - 0.55) / 0.3025 - center * 0.65;
                }).ToArray();
                mark = AreaTransform(tight, (x, y) => (x * 0.65 + shifts[(int)y], y * 0.85));
                ValidateHook(mark);
                int bottom = b.y + b.h - 1;
                int bandTop = Math.Max(b.y, bottom - Math.Max(1, b.h / 6));
                // Optical anchor: rightmost maximum-coverage pixel in the lower contour band.
                // Do not use the last faint row of a rounded bowl; it can be far left of a's leg.
                var candidates = Enumerable.Range(bandTop, bottom - bandTop + 1).SelectMany(y => Enumerable.Range(b.x, b.w).Select(x => (x, y))).ToArray();
                byte maxAlpha = candidates.Max(p => basis.Image.Pixels[(p.y * basis.Image.W + p.x) * 4 + 3]);
                var anchor = candidates.Where(p => basis.Image.Pixels[(p.y * basis.Image.W + p.x) * 4 + 3] == maxAlpha)
                    .OrderByDescending(p => p.x).ThenByDescending(p => p.y).First();
                int overlap = Math.Max(1, mark.H / 5), inset = 1;
                int attachRight = Enumerable.Range(0, overlap + 1).SelectMany(y => Enumerable.Range(0, mark.W).Where(x => mark.Ink(x, y))).Max();
                x0 = anchor.x - inset - attachRight;
                y0 = bottom - overlap;
                geometry = new { anchorX = anchor.x, anchorY = anchor.y, bandTop, maxAlpha, overlap, inset, attachRight, x = x0, y = y0 };
                transform = new { kind = "row-curve-area/v2", scaleX = 0.65, scaleY = 0.85, turn = 0.55, bend = 0.40, rowBoundaryShifts = shifts };
            }
            else
            {
                operation = "slash-axis-compress-rotate-area-base-wins";
                Need(b.h >= 6, "STROKE_BASE_TOO_SHORT");
                Need(float.IsFinite(emSize) && emSize >= 6 && emSize <= 512 && basis.Shift > 0, "STROKE_METRICS");
                int em = (int)Math.Floor(emSize), mid = b.y + b.h / 2;
                // The longest contiguous run at mid-height measures the actual stem, not the foot of L.
                int stemX = -1, stem = 0, run = 0;
                for (int x = 0; x < basis.Image.W; x++)
                {
                    run = basis.Image.Ink(x, mid) ? run + 1 : 0;
                    if (run > stem) { stem = run; stemX = x - run + 1; }
                }
                Need(stem > 0, "STROKE_NO_MID_STEM");
                // Fit all alpha-weighted row centres; single faint cap pixels are not a stable axis.
                double mass = 0, sx = 0, sy = 0, sxy = 0, syy = 0;
                for (int yy = 0; yy < tight.H; yy++) for (int xx = 0; xx < tight.W; xx++)
                { double a = tight.Pixels[(yy * tight.W + xx) * 4 + 3]; mass += a; sx += a * xx; sy += a * yy; sxy += a * xx * yy; syy += a * yy * yy; }
                double slope = (sxy - sx * sy / mass) / (syy - sy * sy / mass);
                double dx = -slope * (tight.H - 1), dy = -(tight.H - 1);
                Need(dx > 0, "SLASH_NOT_RISING_RIGHT");
                double axis = Math.Sqrt(dx * dx + dy * dy), ux = dx / axis, uy = dy / axis;
                // Projection envelope measures transverse thickness including all original AA.
                var ink = Enumerable.Range(0, tight.W * tight.H).Where(i => tight.Pixels[i * 4 + 3] != 0).ToArray();
                double thickness = ink.Max(i => -uy * (i % tight.W) + ux * (i / tight.W)) - ink.Min(i => -uy * (i % tight.W) + ux * (i / tight.W)) + 1;
                int radius = (int)Math.Ceiling(thickness / 2);
                // Full transverse envelope, including up to one AA pixel at each edge.
                // Unlike the removed solid nib, the donor's coverage fringe is never thresholded.
                Need(thickness <= Math.Max(2, b.h / 3.0) + 2 && thickness <= Math.Max(2, em / 4.0) + 2 && radius <= stem,
                    "STROKE_TOO_THICK");
                int length = Math.Max(6, Math.Max(Math.Min(3 * stem, basis.Shift + 2 * radius), b.h / 3));
                Need(length <= b.h && length <= em && length <= basis.Shift + 2 * radius,
                    "STROKE_TOO_LONG");
                double scale = Math.Min(1, length / axis), vx = 0.8660254037844386, vy = -0.5;
                mark = AreaTransform(tight, (x, y) => {
                    double along = (x * ux + y * uy) * scale, across = -x * uy + y * ux;
                    return (along * vx - across * vy, along * vy + across * vx);
                });
                ValidateStroke(mark);
                x0 = stemX + (stem - mark.W) / 2; y0 = mid - mark.H / 2;
                geometry = new { length, mid, thickness, radius, stem, stemX, x = x0, y = y0, emSize };
                transform = new { kind = "axis-compress-rotate-area/v1", ux, uy, axisScale = scale, transverseScale = 1, targetDegrees = 30, vx, vy };
            }
            donorTransform = new { donorCode = hook ? 0x002C : 0x002F, donorUnicode = hook ? "U+002C" : "U+002F",
                donorRgbaSha256 = FontBuilderCore.Hash(source.Pixels), crop = new { x = crop.x, y = crop.y, width = crop.w, height = crop.h },
                cropRgbaSha256 = FontBuilderCore.Hash(tight.Pixels), transform,
                sampling = "single-pass-exact-cell-area-premultiplied-to-straight-round-half-up", transparentTrimOnly = true };
            for (int y = 0; y < mark.H; y++) for (int x = 0; x < mark.W; x++)
                if (mark.Ink(x, y)) points.Add((x0 + x, y0 + y, y * mark.W + x));
            if (!hook) Need(points.Min(p => p.y) >= b.y + 1 && points.Max(p => p.y) < b.y + b.h - 1, "STROKE_CLIPPING");
            Need(points.Any(p => p.x >= 0 && p.x < basis.Image.W && p.y >= 0 && p.y < basis.Image.H && basis.Image.Ink(p.x, p.y)),
                hook ? "HOOK_DOES_NOT_JOIN_BASE" : "STROKE_DOES_NOT_CROSS_BASE");
        }
        bool stroke = "łŁ".Contains(character);
        int left = Math.Min(0, points.Min(p => p.x) - (stroke ? 1 : 0));
        int w = Math.Max(basis.Image.W, points.Max(p => p.x) + 1 + (stroke ? 1 : 0)) - left;
        int h = Math.Max(basis.Image.H, points.Max(p => p.y) + 1);
        Need(points.Min(p => p.y) >= 0 && h <= maxHeight && w <= 512, "MARK_OUTSIDE_FONT_ENVELOPE");
        int offset = checked(basis.Offset + left);
        int shift = basis.Shift;
        if (character == 'ł')
        {
            // Optical spacing, independent of the slash's alpha edge/canvas.
            // Move the WHOLE unchanged bitmap in pen space; allow stroke overhang.
            // One 1/32-em unit, round-half-up to signed integer FONT metrics.
            int optical = Math.Max(1, (int)Math.Floor(emSize / 32.0 + 0.5));
            offset = checked(offset + optical);
            shift = checked(basis.Shift + optical);
        }
        else if (stroke)
        {
            // Exclusive alpha edge in pen coordinates (including AA=1), not canvas padding.
            // Base-wins overlay preserves the union of base and mark alpha. Keep one empty
            // pixel before the next pen, consuming existing advance before expanding it.
            int right = checked(basis.Offset + Math.Max(b.x + b.w, points.Max(p => p.x) + 1));
            shift = Math.Max(basis.Shift, checked(right + 1));
        }
        Need(offset >= short.MinValue && offset <= short.MaxValue && shift <= short.MaxValue, "METRICS_RANGE");
        Need(!stroke || shift - basis.Shift <= Math.Max(1, (int)Math.Floor(emSize) / 2), "STROKE_ADVANCE_EXPANSION");
        var pixels = new byte[w * h * 4];
        for (int y = 0; y < basis.Image.H; y++) Array.Copy(basis.Image.Pixels, y * basis.Image.W * 4, pixels, (y * w - left) * 4, basis.Image.W * 4);
        int added = 0;
        foreach (var p in points)
        {
            int dst = (p.y * w + p.x - left) * 4;
            if (pixels[dst + 3] != 0) { Need(!above, "MARK_OVERLAP"); continue; }
            Array.Copy(mark.Pixels, p.pixel * 4, pixels, dst, 4); added++;
        }
        Need(added > 0, "NO_NEW_PIXELS");
        var g = new Glyph { Character = character, Image = new Bitmap(w, h, pixels), Shift = shift,
            Offset = offset, Kerning = basis.Kerning.Clone() };
        Metrics(g);
        if (!above) Connected(g.Image); // no detached hook or disconnected stroke tabs after base-wins overlay
        return new Composed { Glyph = g, Provenance = new { character = character.ToString(), basis = basis.Character.ToString(), donor,
            recipe = Recipe, operation, geometry, donorTransform, markWidth = mark.W, markHeight = mark.H,
            baseSha256 = FontBuilderCore.Hash(basis.Image.Pixels), markSha256 = FontBuilderCore.Hash(mark.Pixels),
            resultSha256 = FontBuilderCore.Hash(pixels), baseX = -left, baseY = 0, verticalTranslation = 0,
            baselinePolicy = "original-row-origin-no-vertical-bearing-in-FONT", addedPixels = added, maxHeight,
            shift = g.Shift, offset = g.Offset, shiftDelta = shift - basis.Shift,
            kerningPolicy = "copy-base-incoming-only" } };
    }
    public static (Bitmap image, JsonElement font) Pack(string name, IEnumerable<Composed> input)
    {
        var glyphs = input.OrderBy(g => g.Glyph.Character).ToArray();
        Need(glyphs.Length > 0 && glyphs.Length <= 18 && glyphs.Select(g => g.Glyph.Character).Distinct().Count() == glyphs.Length, "PACK_GLYPHS");
        const int width = 1024;
        int x = 1, y = 1, rowHeight = 0;
        var positions = new List<(Glyph g, int x, int y)>();
        foreach (var item in glyphs)
        {
            var g = item.Glyph; Metrics(g); Need(g.Image.W + 2 <= width, "PACK_WIDTH");
            if (x + g.Image.W + 1 > width) { x = 1; y += rowHeight + 1; rowHeight = 0; }
            positions.Add((g, x, y)); x += g.Image.W + 1; rowHeight = Math.Max(rowHeight, g.Image.H);
        }
        int height = y + rowHeight + 1; Need(height <= 4096, "PACK_HEIGHT");
        var pixels = new byte[width * height * 4];
        foreach (var p in positions) for (int row = 0; row < p.g.Image.H; row++)
            Array.Copy(p.g.Image.Pixels, row * p.g.Image.W * 4, pixels, ((p.y + row) * width + p.x) * 4, p.g.Image.W * 4);
        return (new Bitmap(width, height, pixels), JsonSerializer.SerializeToElement(new { name,
            atlas = new { file = name + ".rgba", format = "rgba8", width, height, sha256 = FontBuilderCore.Hash(pixels) },
            glyphs = positions.Select(p => new { character = p.g.Character.ToString(), x = p.x, y = p.y, width = p.g.Image.W, height = p.g.Image.H,
                shift = p.g.Shift, offset = p.g.Offset, kerning = p.g.Kerning }).ToArray() }));
    }
}
