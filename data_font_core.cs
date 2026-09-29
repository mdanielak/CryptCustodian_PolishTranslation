using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text.Json;
using CCDataNoop;
using UndertaleModLib;
using UndertaleModLib.Models;
using UndertaleModLib.Util;

namespace CCDataFont;

public static class FontWriter
{
    // Writer-only override: no-op keeps its original source contract. The game
    // root remains required and independently protected even for an offline copy.
    public static string Source => NoopPolicy.ConfiguredPath("CC_DATA_SOURCE", NoopPolicy.Source);
    public static string[] ProtectedRoots => new[] { NoopPolicy.GameRoot, Path.GetDirectoryName(Source), BundlePath };
    public static string BundlePath => NoopPolicy.ConfiguredPath("CC_FONT_BUNDLE");
    public static readonly string[] Names = { "Nerko", "NerkoLarge", "NerkoLarge2", "NerkoSmall" };
    public static readonly int[] OldTpag = { 142, 136, 137, 147 }, OldTxtr = { 13, 14, 14, 13 };
    public static readonly (int W, int H)[] OldSize = { (512,512), (1024,1024), (1024,1024), (512,256) };
    public static readonly Dictionary<string, (int Size, string Hash)> Pins = new()
    {
        // Final-verification bundle atlas-001 (integration run 001), stroke variant B.
        // Its manifest, report, and all four RGBA payloads are exact fixed inputs.
        ["manifest.json"] = (15394,"f9374aca5971bc9a4bda50b904802c29a9beca794e54d9038f2dcf292f322cfa"),
        ["report.json"] = (170447,"e700d721279e24080b6f07f5f174ec2cad06a2057f469c6ffa3d80b4ad6c3d3d"),
        ["Nerko.rgba"] = (184320,"2214e509588c3c0396c8b257227fdcf2a62d2962c1291c8e7541d2da38206552"),
        ["NerkoLarge.rgba"] = (499712,"18b2a1c1a3a5d086deed190c1f016d049b6a78db1422765433d13ef282d76aa9"),
        ["NerkoLarge2.rgba"] = (446464,"9932e727638c1393fb98ddc4dff1e7d75a26f1cd01256c6a42bbb7dd9762e515"),
        ["NerkoSmall.rgba"] = (159744,"e87f45966b34a846fbb725816b7065007d59d241065061295c52f41d967ecfaf")
    };
    public static void Need(bool ok, string why) => NoopPolicy.Require(ok, why);
    public static byte[] Json(object x) => JsonSerializer.SerializeToUtf8Bytes(x, new JsonSerializerOptions { WriteIndented = true });
    public static int N(JsonElement x, string key) => x.GetProperty(key).GetInt32();
    public static string S(JsonElement x, string key) => x.GetProperty(key).GetString();
    public static void Pin(byte[] bytes, int size, string hash) => Need(bytes.Length == size && FrozenBytes.Hash(bytes) == hash, "BUNDLE_HASH_SIZE");
    public static void ValidateSourceIdentity(JsonElement report)
    {
        // Historical location is provenance, not the identity of a read-only copy.
        // Never rewrite the pinned report or require its old location to exist.
        FontBuilderCore.Absolute(S(report,"source"));
        Need(S(report,"sourceSha256") == NoopPolicy.SourceSha256 && N(report,"sourceBytes") == 182801522, "BUNDLE_SOURCE");
    }
    public static FrozenBytes ReadSource()
    {
        FontBuilderIO.NoLinks(Source);
        var bytes=FrozenBytes.ReadFile(Source); bytes.RequireHash(NoopPolicy.SourceSha256);
        Need(bytes.Length==182801522,"SOURCE_SIZE"); return bytes;
    }
    public sealed record Bundle(JsonElement Manifest, JsonElement Report, Dictionary<string, byte[]> Files);
    public static Bundle LoadBundle()
    {
        FontBuilderIO.NoLinks(BundlePath);
        Need(Directory.GetFileSystemEntries(BundlePath).Select(Path.GetFileName).Order().SequenceEqual(Pins.Keys.Order()), "BUNDLE_FILE_SET");
        var files = new Dictionary<string, byte[]>();
        foreach (var p in Pins) { var b = FontBuilderIO.Read(Path.Combine(BundlePath,p.Key), 1024*1024); Pin(b,p.Value.Size,p.Value.Hash); files.Add(p.Key,b); }
        var manifest = FontBuilderCore.Parse(files["manifest.json"]); var report = FontBuilderCore.Parse(files["report.json"]);
        Need(S(report,"schema") == "cc-font-atlas-report/v1" && S(report,"status") == "candidate" && S(report,"recipe") == "existing-pixels/v3", "BUNDLE_REPORT");
        ValidateSourceIdentity(report);
        Need(report.GetProperty("outputs").GetArrayLength() == 5 && report.GetProperty("provenance").GetArrayLength() == 4, "BUNDLE_COUNTS");
        Need(report.GetProperty("outputs").EnumerateArray().Select(o=>S(o,"file")).Order().SequenceEqual(Pins.Keys.Where(k=>k!="report.json").Order()),"BUNDLE_OUTPUT_SET");
        foreach (var o in report.GetProperty("outputs").EnumerateArray()) Pin(files[S(o,"file")],N(o,"bytes"),S(o,"sha256"));
        Need(report.GetProperty("diagnostics").EnumerateArray().Count(x => S(x,"status") == "preserved") == 8 &&
            report.GetProperty("diagnostics").EnumerateArray().Count(x => S(x,"status") == "composed-offline-candidate") == 64, "BUNDLE_DIAGNOSTICS");
        var bundle=new Bundle(manifest,report,files); ValidateBundleContent(bundle); return bundle;
    }
    public static void ValidateBundleContent(Bundle bundle)
    {
        FontBuilderCore.ValidateManifest(bundle.Manifest,Names,false,
            Names.ToDictionary(n=>n,n=>new[]{(int)'ó',(int)'Ó'}),
            bundle.Files.Where(x=>x.Key.EndsWith(".rgba")).ToDictionary(x=>x.Key,x=>x.Value));
        for(int i=0;i<4;i++)
        {
            var m=bundle.Manifest.GetProperty("fonts")[i]; var p=bundle.Report.GetProperty("provenance")[i];
            Need(S(m,"name")==Names[i] && S(p,"name")==Names[i],"BUNDLE_FONT_ORDER");
            var glyphs=m.GetProperty("glyphs").EnumerateArray().ToArray();
            var additions=p.GetProperty("additions").EnumerateArray().ToArray();
            Need(additions.Select(a=>S(a,"character")).Order().SequenceEqual(glyphs.Select(g=>S(g,"character")).Order()),"BUNDLE_ADDITION_SET");
            var atlas=m.GetProperty("atlas");
            foreach(var g in glyphs)
            {
                var a=additions.Single(a=>S(a,"character")==S(g,"character"));
                var crop=Crop(bundle.Files[S(atlas,"file")],N(atlas,"width"),N(atlas,"height"),N(g,"x"),N(g,"y"),N(g,"width"),N(g,"height"));
                Need(FrozenBytes.Hash(crop)==S(a,"resultSha256") && N(g,"shift")==N(a,"shift") && N(g,"offset")==N(a,"offset"),"BUNDLE_GLYPH_PROVENANCE");
            }
        }
    }
    public sealed record Placement(ushort Code, int X, int Y, int W, int H);
    public sealed record Layout(int Width, int Height, Placement[] Glyphs);
    // Adaptation agreed in conversation: fixed original width, Unicode shelf order,
    // two *empty* pixels between rectangles and at new-page edges; no old-pixel padding.
    public static Layout Pack(int oldW, int oldH, IEnumerable<(ushort Code,int W,int H)> sizes)
    {
        Need(oldW > 4 && oldW <= 4096 && oldH > 0 && oldH <= 4096, "LAYOUT_OLD_BOUNDS");
        var items = sizes.OrderBy(x => x.Code).ToArray(); Need(items.Length == 16 && items.Select(x => x.Code).Distinct().Count() == 16, "LAYOUT_CODES");
        int x=2, y=checked(oldH+2), row=0; var positions = new List<Placement>();
        foreach (var g in items)
        {
            Need(g.W > 0 && g.W <= oldW-4 && g.H > 0 && g.H <= 512, "LAYOUT_GLYPH_BOUNDS");
            if (checked(x+g.W+2) > oldW) { x=2; y=checked(y+row+2); row=0; }
            positions.Add(new(g.Code,x,y,g.W,g.H)); x=checked(x+g.W+2); row=Math.Max(row,g.H);
        }
        int needed=checked(y+row+2), h=1;
        while(h < needed) h=checked(h*2);
        Need(h <= 4096, "LAYOUT_OVERFLOW");
        var result = new Layout(oldW,h,positions.ToArray()); ValidateLayout(result,oldH); return result;
    }
    public static void ValidateLayout(Layout l, int oldH)
    {
        Need(l.Width > 0 && l.Width <= 4096 && l.Height > oldH && l.Height <= 4096, "PAGE_BOUNDS");
        foreach (var a in l.Glyphs)
        {
            Need(a.W>0 && a.H>0 && a.X>=2 && a.Y>=oldH+2 && (long)a.X+a.W+2<=l.Width && (long)a.Y+a.H+2<=l.Height, "PLACEMENT_BOUNDS");
            foreach(var b in l.Glyphs.Where(b=>b.Code!=a.Code))
                Need((long)a.X+a.W+2<=b.X || (long)b.X+b.W+2<=a.X || (long)a.Y+a.H+2<=b.Y || (long)b.Y+b.H+2<=a.Y, "PLACEMENT_COLLISION");
        }
        Need(l.Glyphs.Select(x=>x.Code).Distinct().Count()==l.Glyphs.Length,"DUPLICATE_PLACEMENT");
    }
    public static short Signed(int x) => checked((short)x);
    public static byte[] Swap(byte[] rgba)
    { var b=rgba.ToArray(); for(int i=0;i<b.Length;i+=4) (b[i],b[i+2])=(b[i+2],b[i]); return b; }
    public static byte[] Crop(byte[] b,int width,int height,int x,int y,int w,int h)
    {
        Need(width>0 && height>0 && b.Length==checked(width*height*4) && x>=0 && y>=0 && w>=0 && h>=0 && (long)x+w<=width && (long)y+h<=height,"CROP_BOUNDS");
        var r=new byte[checked(w*h*4)]; for(int row=0;row<h;row++) Buffer.BlockCopy(b,checked(((y+row)*width+x)*4),r,row*w*4,w*4); return r;
    }
    public static void Blit(byte[] dest,int width,int height,byte[] src,int w,int h,int x,int y)
    {
        Need(dest.Length==checked(width*height*4) && src.Length==checked(w*h*4) && x>=0 && y>=0 && (long)x+w<=width && (long)y+h<=height,"BLIT_BOUNDS");
        for(int row=0;row<h;row++) Buffer.BlockCopy(src,row*w*4,dest,checked(((y+row)*width+x)*4),w*4);
    }
    public static FieldInfo Field(object o,string name)
    {
        for(Type t=o.GetType();t!=null;t=t.BaseType)
        { var f=t.GetField(name,BindingFlags.Instance|BindingFlags.Public|BindingFlags.NonPublic|BindingFlags.DeclaredOnly); if(f!=null) return f; }
        throw new GateRefusal("MISSING_FIELD "+o.GetType().Name+"."+name);
    }
    public static object Get(object o,string name)=>Field(o,name).GetValue(o);
    public static void Set(object o,string name,object value)=>Field(o,name).SetValue(o,value);
    public static object GlyphRecord(UndertaleFont.Glyph g)=>new {g.Character,g.SourceX,g.SourceY,g.SourceWidth,g.SourceHeight,g.Shift,g.Offset,g.UnknownAlwaysZero,kerning=g.Kerning.Select(k=>new{k.Character,k.ShiftModifier}).ToArray()};
    public static byte[] Decode(UndertaleEmbeddedTexture tex,UndertaleTexturePageItem t)
    {
        Need(!tex.TextureExternal && tex.TextureLoaded && tex.GeneratedMips==0,"TEXTURE_FLAGS");
        return Swap(FontAtlasBz2Qoi.Decode(tex.TextureData.Image,t,tex.TextureWidth,tex.TextureHeight,true));
    }
    public static UndertaleTexturePageItem Page(int w,int h,UndertaleEmbeddedTexture tex,int id,int textureId)
    {
        var p=new UndertaleTexturePageItem {Name=new UndertaleString("PageItem "+id),SourceWidth=checked((ushort)w),SourceHeight=checked((ushort)h),
            TargetWidth=checked((ushort)w),TargetHeight=checked((ushort)h),BoundingWidth=checked((ushort)w),BoundingHeight=checked((ushort)h),TexturePage=tex};
        ((UndertaleResourceById<UndertaleEmbeddedTexture,UndertaleChunkTXTR>)Get(p,"_texturePage")).CachedId=textureId; return p;
    }
    public static void Group(UndertaleData d)
    {
        Need(d != null && d.FORM != null,"GROUP_FORM");
        Need(d.TextureGroupInfo != null && d.TextureGroupInfo.Count>3 && d.TextureGroupInfo[3].Name.Content=="default" && d.TextureGroupInfo[3].LoadType==UndertaleTextureGroupInfo.TextureGroupLoadType.InFile,"GROUP_DEFAULT");
        var g=d.TextureGroupInfo[3];
        Need(g.TexturePages.Select(x=>x.Resource).Distinct().Count()==g.TexturePages.Count,"GROUP_DUPLICATES");
        for(int i=0;i<g.TexturePages.Count;i++) Need(g.TexturePages[i].Resource.TextureInfo==g && g.TexturePages[i].Resource.IndexInGroup==i,"GROUP_INDEX");
        foreach(int font in Enumerable.Range(11,4)) Need(g.Fonts.Count(x=>ReferenceEquals(x.Resource,d.Fonts[font]))==1,"GROUP_FONT");
    }
    public sealed record PlannedFont(int FontId,int OldPageId,int OldTextureId,Layout Layout,byte[] OldCrop,byte[] Pixels,
        UndertaleTexturePageItem Page,UndertaleEmbeddedTexture Texture,UndertaleFont.Glyph[] Additions,ushort[] OldCodes,JsonElement Manifest);
    public sealed class Plan
    {
        public int TpagCount,TxtrCount,GroupCount;
        public PlannedFont[] Fonts;
        public byte[][] EncodedEnvelopes;
        public object Evidence => new {layout="old-width/pow2-height/shelf-unicode/gutter-2/v1",TpagCount,TxtrCount,GroupCount,
            fonts=Fonts.Select(p=>new{p.FontId,p.OldPageId,p.OldTextureId,newPage=TpagCount+p.FontId-11,newTexture=TxtrCount+p.FontId-11,
                p.Layout,oldCropSha256=FrozenBytes.Hash(p.OldCrop),pageRgbaSha256=FrozenBytes.Hash(p.Pixels),oldGlyphs=p.OldCodes.Length,added=p.Additions.Length,coverage=18}).ToArray()};
    }
    public static Plan Prepare(UndertaleData d, Bundle b)
    {
        Need(d.IsVersionAtLeast(2022,9) && d.Fonts.Count==16,"GAME_VERSION_FONTS"); Group(d);
        Need(d.EmbeddedTextures.Count+4<=short.MaxValue,"TXTR_ID_OVERFLOW");
        var existing=Names.Select((n,i)=>(n,codes:d.Fonts[11+i].Glyphs.Select(g=>(int)g.Character).ToArray())).ToDictionary(x=>x.n,x=>x.codes);
        FontBuilderCore.ValidateManifest(b.Manifest,Names,false,existing,b.Files.Where(x=>x.Key.EndsWith(".rgba")).ToDictionary(x=>x.Key,x=>x.Value));
        var plan=new Plan {TpagCount=d.TexturePageItems.Count,TxtrCount=d.EmbeddedTextures.Count,GroupCount=d.TextureGroupInfo[3].TexturePages.Count};
        var fonts=new List<PlannedFont>(); var decoded=new Dictionary<int,byte[]>();
        for(int i=0;i<4;i++)
        {
            var f=d.Fonts[11+i]; var t=f.Texture; var tex=t.TexturePage; var m=b.Manifest.GetProperty("fonts")[i]; var p=b.Report.GetProperty("provenance")[i];
            Need(f.Name.Content==Names[i] && S(m,"name")==Names[i] && S(p,"name")==Names[i] && N(p,"fontIndex")==11+i &&
                d.TexturePageItems.IndexOf(t)==OldTpag[i] && d.EmbeddedTextures.IndexOf(tex)==OldTxtr[i],"FONT_TOPOLOGY");
            Need(f.SDFSpread==0 && f.ScaleX==1 && f.ScaleY==1 && t.SourceWidth==OldSize[i].W && t.SourceHeight==OldSize[i].H,"FONT_LAYOUT");
            FontAtlasUmt.RequireIdentityLayout(t);
            Need(f.Glyphs.Select(g=>g.Character).SequenceEqual(f.Glyphs.Select(g=>g.Character).Order()) && f.Glyphs.Select(g=>g.Character).Distinct().Count()==f.Glyphs.Count,"OLD_CODE_ORDER");
            Need(FrozenBytes.Hash(Json(f.Glyphs.Select(GlyphRecord).ToArray()))==S(p,"oldGlyphModelSha256"),"OLD_GLYPH_PROVENANCE");
            foreach(var field in p.GetProperty("tpag").EnumerateObject()) Need(Convert.ToInt32(t.GetType().GetProperty(field.Name).GetValue(t))==field.Value.GetInt32(),"TPAG_PROVENANCE");
            if(!decoded.TryGetValue(OldTxtr[i],out var full)) decoded.Add(OldTxtr[i],full=Decode(tex,t));
            Need(FrozenBytes.Hash(full)==S(p,"pageRgbaSha256"),"PAGE_PROVENANCE");
            byte[] old=Crop(full,tex.TextureWidth,tex.TextureHeight,t.SourceX,t.SourceY,t.SourceWidth,t.SourceHeight);
            var glyphs=m.GetProperty("glyphs").EnumerateArray().ToArray();
            var layout=Pack(t.SourceWidth,t.SourceHeight,glyphs.Select(g=>((ushort)S(g,"character")[0],N(g,"width"),N(g,"height"))));
            var pixels=new byte[checked(layout.Width*layout.Height*4)]; Blit(pixels,layout.Width,layout.Height,old,t.SourceWidth,t.SourceHeight,0,0);
            var added=new List<UndertaleFont.Glyph>(); var atlas=m.GetProperty("atlas");
            foreach(var pos in layout.Glyphs)
            {
                var g=glyphs.Single(g=>S(g,"character")[0]==pos.Code);
                Need(!f.Glyphs.Any(g=>g.Character==pos.Code),"EXISTING_GLYPH_COLLISION");
                byte[] crop=Crop(b.Files[S(atlas,"file")],N(atlas,"width"),N(atlas,"height"),N(g,"x"),N(g,"y"),pos.W,pos.H);
                var provenance=p.GetProperty("additions").EnumerateArray().Single(a=>S(a,"character")[0]==pos.Code);
                Need(FrozenBytes.Hash(crop)==S(provenance,"resultSha256"),"ADDITION_PROVENANCE");
                Blit(pixels,layout.Width,layout.Height,crop,pos.W,pos.H,pos.X,pos.Y);
                var ng=new UndertaleFont.Glyph {Character=pos.Code,SourceX=checked((ushort)pos.X),SourceY=checked((ushort)pos.Y),SourceWidth=checked((ushort)pos.W),SourceHeight=checked((ushort)pos.H),Shift=Signed(N(g,"shift")),Offset=Signed(N(g,"offset")),UnknownAlwaysZero=0};
                foreach(var k in g.GetProperty("kerning").EnumerateArray()) ng.Kerning.Add(new(){Character=Signed(N(k,"character")),ShiftModifier=Signed(N(k,"shiftModifier"))});
                added.Add(ng);
            }
            var raw=new GMImage(layout.Width,layout.Height); Swap(pixels).AsSpan().CopyTo(raw.GetRawImageData());
            var nt=new UndertaleEmbeddedTexture {Name=new UndertaleString("Texture "+(plan.TxtrCount+i)),Scaled=tex.Scaled,GeneratedMips=0,
                TextureWidth=layout.Width,TextureHeight=layout.Height,IndexInGroup=plan.GroupCount+i,TextureInfo=d.TextureGroupInfo[3],TextureExternal=false,TextureLoaded=true};
            nt.TextureData.Image=raw.ConvertToBz2Qoi();
            var np=Page(layout.Width,layout.Height,nt,plan.TpagCount+i,plan.TxtrCount+i);
            Need(Decode(nt,np).SequenceEqual(pixels),"ENCODE_BOUNDED_ROUNDTRIP");
            fonts.Add(new(11+i,OldTpag[i],OldTxtr[i],layout,old,pixels,np,nt,added.ToArray(),f.Glyphs.Select(g=>g.Character).ToArray(),m));
        }
        plan.Fonts=fonts.ToArray();
        plan.EncodedEnvelopes=plan.Fonts.Select(f=>Relocations.Envelope(f.Texture.TextureData.Image)).ToArray();
        return plan;
    }
    public static void Apply(UndertaleData d,Plan p)
    {
        Need(d.TexturePageItems.Count==p.TpagCount && d.EmbeddedTextures.Count==p.TxtrCount && d.TextureGroupInfo[3].TexturePages.Count==p.GroupCount,"APPLY_COUNTS");
        for(int i=0;i<4;i++)
        {
            var a=p.Fonts[i]; var f=d.Fonts[a.FontId];
            Need(f.Texture==d.TexturePageItems[a.OldPageId] && f.Glyphs.Select(g=>g.Character).SequenceEqual(a.OldCodes),"APPLY_BASELINE");
            Need(!a.Additions.Any(n=>f.Glyphs.Any(g=>g.Character==n.Character)),"EXISTING_GLYPH_COLLISION");
            // Preserve all old objects and their exact fields; sorted insertion, not cloning.
            foreach(var n in a.Additions) { int index=0; while(index<f.Glyphs.Count && f.Glyphs[index].Character<n.Character) index++; f.Glyphs.Insert(index,n); }
            d.TexturePageItems.Add(a.Page); d.EmbeddedTextures.Add(a.Texture); f.Texture=a.Page;
            d.TextureGroupInfo[3].TexturePages.Add(new(a.Texture,p.TxtrCount+i));
        }
    }
    // Exact inverse of authorized mutations, used for full graph proof, NOT serialization.
    // New nodes are independently covered by expected/readback full-snapshot equality.
    public static void Undo(UndertaleData d,Plan p)
    {
        Need(d.TexturePageItems.Count==p.TpagCount+4 && d.EmbeddedTextures.Count==p.TxtrCount+4 && d.TextureGroupInfo[3].TexturePages.Count==p.GroupCount+4,"UNDO_COUNTS");
        for(int i=0;i<4;i++)
        {
            var a=p.Fonts[i]; var f=d.Fonts[a.FontId];
            Need(ReferenceEquals(f.Texture,d.TexturePageItems[p.TpagCount+i]),"FONT_NEW_REFERENCE");
            foreach(var n in a.Additions) { var matches=f.Glyphs.Where(g=>g.Character==n.Character).ToArray(); Need(matches.Length==1,"ADDITION_COUNT"); f.Glyphs.Remove(matches[0]); }
            Need(f.Glyphs.Select(g=>g.Character).SequenceEqual(a.OldCodes),"PRESERVED_CODE_ORDER"); f.Texture=d.TexturePageItems[a.OldPageId];
        }
        for(int i=0;i<4;i++) { d.TexturePageItems.RemoveAt(p.TpagCount); d.EmbeddedTextures.RemoveAt(p.TxtrCount); d.TextureGroupInfo[3].TexturePages.RemoveAt(p.GroupCount); }
    }
    public static object Pixels(UndertaleData d,Plan p,Bundle b)
    {
        Group(d); var result=new List<object>();
        for(int i=0;i<4;i++)
        {
            var a=p.Fonts[i]; var f=d.Fonts[a.FontId]; var t=f.Texture; var tex=t.TexturePage;
            Need(ReferenceEquals(t,d.TexturePageItems[p.TpagCount+i]) && ReferenceEquals(tex,d.EmbeddedTextures[p.TxtrCount+i]) &&
                ReferenceEquals(d.TextureGroupInfo[3].TexturePages[p.GroupCount+i].Resource,tex) && tex.TextureInfo==d.TextureGroupInfo[3] && tex.IndexInGroup==p.GroupCount+i,"NEW_TOPOLOGY");
            byte[] pixels=Decode(tex,t); Need(pixels.SequenceEqual(a.Pixels),"FULL_PAGE_PIXELS");
            Need(Crop(pixels,a.Layout.Width,a.Layout.Height,0,0,OldSize[i].W,OldSize[i].H).SequenceEqual(a.OldCrop),"OLD_CROP_PIXELS");
            foreach(var g in f.Glyphs.Where(g=>a.OldCodes.Contains(g.Character))) Need(
                Crop(pixels,a.Layout.Width,a.Layout.Height,g.SourceX,g.SourceY,g.SourceWidth,g.SourceHeight).SequenceEqual(
                Crop(a.OldCrop,OldSize[i].W,OldSize[i].H,g.SourceX,g.SourceY,g.SourceWidth,g.SourceHeight)),"OLD_GLYPH_PIXELS");
            foreach(var pos in a.Layout.Glyphs)
            {
                var mg=a.Manifest.GetProperty("glyphs").EnumerateArray().Single(g=>S(g,"character")[0]==pos.Code); var atlas=a.Manifest.GetProperty("atlas");
                Need(Crop(pixels,a.Layout.Width,a.Layout.Height,pos.X,pos.Y,pos.W,pos.H).SequenceEqual(Crop(b.Files[S(atlas,"file")],N(atlas,"width"),N(atlas,"height"),N(mg,"x"),N(mg,"y"),pos.W,pos.H)),"BUNDLE_ADDITION_PIXELS");
            }
            Need(FontBuilderCore.Required.All(c=>f.Glyphs.Count(g=>g.Character==c)==1),"COVERAGE_18");
            result.Add(new{font=Names[i],oldCrop=true,oldGlyphs=a.OldCodes.Length,additions=16,coverage=18,wholePageSha256=FrozenBytes.Hash(pixels)});
        }
        return result;
    }
}
