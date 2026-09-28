using System.Buffers.Binary;
using System.IO.Compression;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using CCDataNoop;
using UndertaleModLib;
using static CCDataFont.FontWriter;

namespace CCDataFont;

public static class OutputIO
{
    [DllImport("kernel32.dll", EntryPoint="CreateDirectoryW", ExactSpelling=true,CharSet=CharSet.Unicode,SetLastError=true)]
    [return:MarshalAs(UnmanagedType.Bool)] static extern bool CreateDirectoryExclusive(string path,IntPtr security);
    public static string Reserve(string prefix)
    {
        Need(new[]{"cc-data-font-","cc-data-font-tests-"}.Contains(prefix),"RUN_PREFIX");
        FontBuilderIO.CheckExternalDirectory(NoopPolicy.TempRoot, NoopPolicy.GameRoot);
        for(int i=1;i<=999999;i++)
        {
            string run=Path.Combine(NoopPolicy.TempRoot,prefix+i.ToString("D3"));
            if(!CreateDirectoryExclusive(run,IntPtr.Zero)) { int e=Marshal.GetLastWin32Error(); if(e==183) continue; throw new IOException("CREATE_RUN "+e); }
            string release=Path.Combine(run,"release");
            Need(CreateDirectoryExclusive(release,IntPtr.Zero),"CREATE_RELEASE");
            // Includes physical local volume, 8.3, reparse and whole-game exclusion.
            FontBuilderIO.CheckNewOutputDirectory(Path.Combine(release,"data.win"),Path.GetDirectoryName(NoopPolicy.Source));
            return release;
        }
        throw new GateRefusal("NO_FREE_RUN");
    }
    public static void WriteNew(string path,Action<FileStream> write)
    {
        // This existing path guard checks an absent leaf and its physical release
        // parent (no creation); unlike CheckOutput it is not JSON-only.
        FontBuilderIO.CheckNewOutputDirectory(path,Path.GetDirectoryName(NoopPolicy.Source));
        using var s=new FileStream(path,FileMode.CreateNew,FileAccess.ReadWrite,FileShare.None);
        write(s); s.Flush(true);
    }
    public static void SaveJson(string release,string name,object value)
    {
        var b=Json(value); string path=Path.Combine(release,name); WriteNew(path,s=>s.Write(b));
        Need(FrozenBytes.ReadFile(path).Span.SequenceEqual(b),"JSON_READBACK"); FontBuilderCore.Parse(b);
    }
    public static object Snapshot(string release,string name,GraphSnapshot snapshot)
    {
        string path=Path.Combine(release,name);
        WriteNew(path,file=> {
            using var gzip=new GZipStream(file,CompressionLevel.Fastest,true);
            using var text=new StreamWriter(gzip,new UTF8Encoding(false,true));
            foreach(var row in snapshot.Rows) text.WriteLine(JsonSerializer.Serialize(row));
        });
        using(var file=File.OpenRead(path)) using(var gzip=new GZipStream(file,CompressionMode.Decompress)) using(var text=new StreamReader(gzip,new UTF8Encoding(false,true)))
        {
            foreach(var row in snapshot.Rows) Need(JsonSerializer.Deserialize<SnapshotRow>(text.ReadLine() ?? throw new GateRefusal("SNAPSHOT_TRUNCATED"))==row,"SNAPSHOT_READBACK");
            Need(text.ReadLine()==null,"SNAPSHOT_TRAILING");
        }
        var bytes=FrozenBytes.ReadFile(path); return new{file=name,rows=snapshot.Rows.Length,semanticSha256=snapshot.Sha256,size=bytes.Length,compressedSha256=bytes.Sha256};
    }
}

// Raw STRG proof, independent of the UMT reader (which may not retain every byte).
// No decoding/normalization of strings: compare exact length-prefixed bytes.
public static class StrgRelocation
{
    public sealed record Layout(int Count,int[] RelativePointers,int RecordsStart,int RecordsEnd,int Padding,int GapBytes);
    public sealed record Evidence(int Count,int UniqueRecords,int RelativeRecordsEnd,int GapBytes,
        int BeforeOffset,int AfterOffset,int BeforeLength,int AfterLength,int BeforePadding,int AfterPadding,string RecordsSha256);
    static int I(FrozenBytes b,int p)=>BinaryPrimitives.ReadInt32LittleEndian(b.Span.Slice(p,4));
    static Layout Parse(FrozenBytes b,ChunkLayout c)
    {
        Need(c.Name=="STRG" && c.Offset>=8 && c.Length>=4 && (long)c.Offset+8+c.Length<=b.Length,"STRG_BOUNDS");
        Need(b.Span.Slice(c.Offset,4).SequenceEqual("STRG"u8) && I(b,c.Offset+4)==c.Length,"STRG_HEADER");
        int end=checked(c.Offset+8+c.Length),count=I(b,c.Offset+8);
        Need(count>=0 && 4L+4L*count<=c.Length,"STRG_COUNT");
        int tableEnd=checked(c.Offset+12+4*count); var pointers=new int[count];
        for(int i=0;i<count;i++)
        {
            int p=I(b,c.Offset+12+4*i);
            // This pinned format has no null slots. Aliases are permitted only if
            // their exact ordered relative pointers survive the round-trip.
            Need(p>=tableEnd && p<=end-5,"STRG_POINTER_BOUNDS"); pointers[i]=p-c.Offset;
        }
        int cursor=tableEnd,gaps=0;
        foreach(int relative in pointers.Distinct().Order())
        {
            int p=checked(c.Offset+relative); Need(p>=cursor,"STRG_RECORD_OVERLAP");
            Need(b.Span.Slice(cursor,p-cursor).IndexOfAnyExcept((byte)0)<0,"STRG_GAP_NONZERO");
            gaps=checked(gaps+p-cursor);
            int length=I(b,p); Need(length>=0 && (long)p+4+length<end,"STRG_RECORD_LENGTH");
            int terminator=checked(p+4+length); Need(b.Span[terminator]==0,"STRG_TERMINATOR");
            cursor=terminator+1;
        }
        // Derive padding from the last parsed record, NOT from a zero suffix:
        // an empty string / NUL terminator is part of the record, not padding.
        long aligned=((long)cursor+127)/128*128;
        Need(end==aligned,"STRG_ALIGNMENT_128");
        Need(b.Span.Slice(cursor,end-cursor).IndexOfAnyExcept((byte)0)<0,"STRG_PADDING_NONZERO");
        return new(count,pointers,tableEnd-c.Offset,cursor-c.Offset,end-cursor,gaps);
    }
    public static Evidence Validate(FrozenBytes source,FrozenBytes output,ChunkLayout before,ChunkLayout after)
    {
        var a=Parse(source,before); var b=Parse(output,after);
        Need(a.Count==b.Count,"STRG_COUNT_CHANGED");
        Need(a.RelativePointers.SequenceEqual(b.RelativePointers),"STRG_ORDER_POINTER_ALIAS_CHANGED");
        Need(a.RecordsStart==b.RecordsStart && a.RecordsEnd==b.RecordsEnd && a.GapBytes==b.GapBytes,"STRG_RECORD_LAYOUT_CHANGED");
        var records=source.Span.Slice(before.Offset+a.RecordsStart,a.RecordsEnd-a.RecordsStart);
        Need(records.SequenceEqual(output.Span.Slice(after.Offset+b.RecordsStart,b.RecordsEnd-b.RecordsStart)),"STRG_RECORD_BYTES_CHANGED");
        Need((long)after.Length-before.Length==b.Padding-a.Padding,"STRG_LENGTH_NOT_PADDING");
        return new(a.Count,a.RelativePointers.Distinct().Count(),a.RecordsEnd,a.GapBytes,before.Offset,after.Offset,
            before.Length,after.Length,a.Padding,b.Padding,FrozenBytes.Hash(records));
    }
    public static Evidence NormalizeLength(UndertaleChunk model,FrozenBytes source,FrozenBytes output,ChunkLayout before,ChunkLayout after)
    {
        Need(model is UndertaleChunkSTRG && model.Length==after.Length,"STRG_READER_LENGTH");
        var proof=Validate(source,output,before,after);
        // Only after the entire raw proof; no other field or graph row is ignored.
        Set(model,"<Length>k__BackingField",(uint)before.Length); return proof;
    }
}

public static class Relocations
{
    public sealed record TextureBlock(int Record,int Start,int Size,int Width,int Height,int GroupIndex);
    static int I(FrozenBytes b,int p) { Need(p>=0 && p<=b.Length-4,"RAW_BOUNDS"); return BinaryPrimitives.ReadInt32LittleEndian(b.Span.Slice(p,4)); }
    public static TextureBlock[] Textures(FrozenBytes b,BinaryLayout raw)
    {
        var c=raw.Chunks.Single(x=>x.Name=="TXTR"); int end=checked(c.Offset+8+c.Length), count=I(b,c.Offset+8);
        Need(count>0 && count<=short.MaxValue && c.Offset+12L+4L*count<=end,"RAW_TXTR_COUNT");
        var list=new List<TextureBlock>();
        for(int i=0;i<count;i++)
        {
            int r=I(b,c.Offset+12+i*4); Need(r>=c.Offset+12+count*4 && r<=end-28,"RAW_TXTR_RECORD");
            int size=I(b,r+8),start=I(b,r+24); Need(size>12 && start>=r+28 && (long)start+size<=end && start%128==0,"RAW_TXTR_BLOB");
            if(i>0) Need(start>=list[^1].Start+list[^1].Size,"RAW_TXTR_OVERLAP");
            list.Add(new(r,start,size,I(b,r+12),I(b,r+16),I(b,r+20)));
        }
        return list.ToArray();
    }
    public static byte[] Envelope(UndertaleModLib.Util.GMImage image)
    {
        using var s=new MemoryStream(); using(var w=new BinaryWriter(s,Encoding.UTF8,true)) image.WriteToBinaryWriter(w,true); return s.ToArray();
    }
    // Only these concrete reader bookkeeping fields can be projected. Values must
    // agree with independently decoded raw offsets/sizes; no wildcard field/chunk ignore.
    public static object Normalize(UndertaleData after,FrozenBytes source,FrozenBytes output,BinaryLayout beforeRaw,BinaryLayout afterRaw,Plan plan)
    {
        Need(beforeRaw.Chunks.Select(c=>c.Name).SequenceEqual(afterRaw.Chunks.Select(c=>c.Name)),"CHUNK_ORDER");
        var changes=new List<object>();
        foreach(var c in beforeRaw.Chunks)
        {
            var n=afterRaw.Chunks.Single(x=>x.Name==c.Name); var model=after.FORM.Chunks[c.Name];
            Need(model.Length==n.Length,"CHUNK_READER_LENGTH");
            // Run even for equal lengths: same-length edits must never evade this gate.
            if(c.Name=="STRG")
            {
                var proof=StrgRelocation.NormalizeLength(model,source,output,c,n);
                changes.Add(new{path="Chunk/STRG/Length",before=c.Length,after=n.Length,reason="verified-strg-relocation-padding",proof});
                continue;
            }
            if(c.Length==n.Length) continue;
            Need(new[]{"FONT","TPAG","TGIN","TXTR"}.Contains(c.Name),"UNALLOWLISTED_CHUNK_LENGTH "+c.Name);
            if(c.Name=="FONT")
            {
                int delta=plan.Fonts.Sum(f=>f.Additions.Sum(g=>4+14+2+(after.IsVersionAtLeast(2024,11)?2:0)+4*g.Kerning.Count));
                Need(delta%16==0 && n.Length-c.Length==delta,"FONT_LENGTH_DELTA");
            }
            if(c.Name=="TPAG")
            {
                int Size(int count)=>checked(((12+26*count+15)/16)*16-8);
                Need(c.Length==Size(plan.TpagCount) && n.Length==Size(plan.TpagCount+4),"TPAG_LENGTH_DELTA");
            }
            if(c.Name=="TGIN") Need(n.Length-c.Length==16,"TGIN_LENGTH_DELTA");
            changes.Add(new{path="Chunk/"+c.Name+"/Length",before=c.Length,after=n.Length,reason="serialized-size"});
            Set(model,"<Length>k__BackingField",(uint)c.Length);
        }
        Need(after.FORM.Length==afterRaw.FormLength,"FORM_READER_LENGTH");
        changes.Add(new{path="FORM/Length",before=beforeRaw.FormLength,after=afterRaw.FormLength,reason="serialized-size"});
        Set(after.FORM,"<Length>k__BackingField",(uint)beforeRaw.FormLength);
        var old=Textures(source,beforeRaw); var now=Textures(output,afterRaw);
        Need(old.Length==plan.TxtrCount && now.Length==old.Length+4 && after.EmbeddedTextures.Count==now.Length,"RAW_TEXTURE_COUNT");
        for(int i=0;i<now.Length;i++)
        {
            var n=now[i]; var tex=after.EmbeddedTextures[i];
            Need((uint)Get(tex,"<_textureBlockSize>k__BackingField")==n.Size && (long)Get(tex.TextureData,"<_maxEndOfStreamPosition>k__BackingField")==n.Start+(long)n.Size,"TXTR_READER_OFFSETS");
            Need(tex.TextureWidth==n.Width && tex.TextureHeight==n.Height && tex.IndexInGroup==n.GroupIndex,"TXTR_READER_HEADER");
            if(i<old.Length)
            {
                var o=old[i];
                Need(source.Span.Slice(o.Record,24).SequenceEqual(output.Span.Slice(n.Record,24)),"OLD_TXTR_HEADER "+i);
                Need(o.Size==n.Size && source.Span.Slice(o.Start,o.Size).SequenceEqual(output.Span.Slice(n.Start,n.Size)),"OLD_TXTR_PAYLOAD "+i);
                Set(tex.TextureData,"<_maxEndOfStreamPosition>k__BackingField",o.Start+(long)o.Size);
            }
            else
            {
                var expected=plan.EncodedEnvelopes[i-old.Length];
                Need(output.Span.Slice(n.Start,n.Size).SequenceEqual(expected),"NEW_TXTR_PAYLOAD "+i);
                Set(tex,"<_textureBlockSize>k__BackingField",0u);
                Set(tex.TextureData,"<_maxEndOfStreamPosition>k__BackingField",-1L);
                changes.Add(new{path="TXTR/"+i+"/_textureBlockSize",before=0,after=n.Size,reason="exact-encoded-envelope"});
            }
            changes.Add(new{path="TXTR/"+i+"/TexData/_maxEndOfStreamPosition",before=i<old.Length?old[i].Start+(long)old[i].Size:-1L,after=n.Start+(long)n.Size,reason="verified-raw-blob-end"});
        }
        // Prove all TXTR gap/trailer bytes zero, not an unbounded payload allowance.
        var tx=afterRaw.Chunks.Single(c=>c.Name=="TXTR"); int cursor=now.Max(n=>n.Record+28);
        foreach(var n in now)
        {
            Need(cursor<=n.Start && output.Span.Slice(cursor,n.Start-cursor).IndexOfAnyExcept((byte)0)<0,"TXTR_PADDING"); cursor=n.Start+n.Size;
        }
        Need(output.Span.Slice(cursor,tx.Offset+8+tx.Length-cursor).IndexOfAnyExcept((byte)0)<0,"TXTR_TRAILER");
        return changes;
    }
}

public static class Host
{
    static string release;
    static readonly Dictionary<string,object> Report=new() { ["schema"]="cc-data-font/v1",["status"]="blocked",["installed"]=false,["gameLaunched"]=false,["writeCalls"]=0 };
    static void Same(GraphSnapshot expected,GraphSnapshot actual,string name)
    {
        var diff=expected.Diff(actual,64); Report[name]=diff; Need(diff.Total==0,name+" differences="+diff.Total+" "+JsonSerializer.Serialize(diff.Items));
    }
    static int Build()
    {
        int exit=2;
        try
        {
            release=OutputIO.Reserve("cc-data-font-"); Console.WriteLine("RELEASE "+release);
            FrozenBytes.ReadFile(typeof(UndertaleData).Assembly.Location).RequireHash(NoopPolicy.UmtSha256);
            FrozenBytes.ReadFile(typeof(Underanalyzer.Decompiler.DecompileSettings).Assembly.Location).RequireHash(NoopPolicy.UnderanalyzerSha256);
            Report["runtime"]=RuntimeInformation.FrameworkDescription; Report["hostSha256"]=FrozenBytes.ReadFile(typeof(Host).Assembly.Location).Sha256;
            Report["umtSha256"]=NoopPolicy.UmtSha256; Report["underanalyzerSha256"]=NoopPolicy.UnderanalyzerSha256;
            var bundle=LoadBundle(); Report["bundle"]=new{path=BundlePath,files=Pins.Select(x=>new{file=x.Key,bytes=x.Value.Size,sha256=x.Value.Hash})};
            FontBuilderIO.NoLinks(NoopPolicy.Source); var source=FrozenBytes.ReadFile(NoopPolicy.Source); source.RequireHash(NoopPolicy.SourceSha256);
            Report["sourceSha256"]=source.Sha256; Report["sourceSize"]=source.Length;
            var rawBefore=BinaryLayout.Parse(source); Report["rawBefore"]=rawBefore;
            using var input=source.OpenRead(); using var data=UndertaleIO.Read(input,NoopPolicy.FatalWarning);
            var (before,coverage)=global::Program.Capture(data,rawBefore); Report["coverageBefore"]=coverage;
            Report["snapshotBefore"]=OutputIO.Snapshot(release,"before.snapshot.jsonl.gz",before);
            Console.WriteLine("BASELINE "+before.Sha256);
            var plan=Prepare(data,bundle); Report["plan"]=plan.Evidence; OutputIO.SaveJson(release,"plan.json",plan.Evidence);
            Apply(data,plan);
            // Proof that the mutator performed only reversible, enumerated edits.
            Undo(data,plan); var undoBefore=global::Program.Capture(data,rawBefore).snapshot;
            Same(before,undoBefore,"mutatorInverseDiff"); undoBefore=null; Apply(data,plan);
            var expected=global::Program.Capture(data,rawBefore).snapshot;
            Report["snapshotExpected"]=OutputIO.Snapshot(release,"expected.snapshot.jsonl.gz",expected);
            Report["prewritePixels"]=Pixels(data,plan,bundle); OutputIO.SaveJson(release,"prewrite.json",Report);
            string path=Path.Combine(release,"data.win"); Report["dataOutput"]=path;
            OutputIO.WriteNew(path,s=> {Report["writeCalls"]=1; UndertaleIO.Write(s,data);});
            var result=FrozenBytes.ReadFile(path); Report["outputSha256"]=result.Sha256; Report["outputSize"]=result.Length;
            // Immediate fresh parser, never the serializer-mutated model.
            using var read=result.OpenRead(); using var after=UndertaleIO.Read(read,NoopPolicy.FatalWarning);
            Report["readbackOk"]=true; Console.WriteLine("READBACK "+result.Sha256+" size="+result.Length);
            var rawAfter=BinaryLayout.Parse(result); Report["rawAfter"]=rawAfter;
            var (actual,coverageAfter)=global::Program.Capture(after,rawAfter); Report["coverageAfter"]=coverageAfter;
            Report["snapshotReadback"]=OutputIO.Snapshot(release,"readback.snapshot.jsonl.gz",actual); actual=null;
            Report["pixels"]=Pixels(after,plan,bundle);
            Report["bookkeepingAllowlist"]=Relocations.Normalize(after,source,result,rawBefore,rawAfter,plan);
            var normalized=global::Program.Capture(after,rawBefore).snapshot;
            Same(expected,normalized,"expectedReadbackDiff"); expected=null; normalized=null;
            Undo(after,plan); var projected=global::Program.Capture(after,rawBefore).snapshot;
            Report["snapshotProtectedProjection"]=OutputIO.Snapshot(release,"protected.snapshot.jsonl.gz",projected);
            Same(before,projected,"protectedResourceDiff");
            Report["status"]="font-candidate-verified"; Report["semanticAllowlistVerified"]=true; exit=0;
        }
        catch(Exception e) { Report["status"]="blocked"; Report["failure"]=e.ToString(); Console.Error.WriteLine("BLOCKED "+e); }
        finally
        {
            if(release!=null) try
            {
                var sourceAfter=FrozenBytes.ReadFile(NoopPolicy.Source); Report["sourceSha256After"]=sourceAfter.Sha256; sourceAfter.RequireHash(NoopPolicy.SourceSha256);
                string output=Path.Combine(release,"data.win");
                if(File.Exists(output)) { using var f=File.OpenRead(output); Report["retainedOutputSize"]=f.Length; Report["retainedOutputSha256"]=Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(f)).ToLowerInvariant(); }
                OutputIO.SaveJson(release,"report.json",Report); Console.WriteLine("REPORT "+Path.Combine(release,"report.json"));
            }
            catch(Exception e) {Console.Error.WriteLine("REPORT_OR_INTEGRITY_FAILED "+e);exit=2;}
        }
        return exit;
    }
    static int Compare(string a,string b)
    {
        foreach(string p in new[]{a,b})
        {
            Need(System.Text.RegularExpressions.Regex.IsMatch(p,"\\A"+System.Text.RegularExpressions.Regex.Escape(NoopPolicy.TempRoot)+@"\\cc-data-font-[0-9]{3,}\\release\z"),"COMPARE_PATH");
            FontBuilderIO.NoLinks(p); var r=FontBuilderCore.Parse(FontBuilderIO.Read(Path.Combine(p,"report.json"),1024*1024)); Need(S(r,"status")=="font-candidate-verified","COMPARE_NOT_VERIFIED");
        }
        Need(a!=b,"COMPARE_INDEPENDENT");
        var left=FrozenBytes.ReadFile(Path.Combine(a,"data.win")); var right=FrozenBytes.ReadFile(Path.Combine(b,"data.win"));
        var diff=BinaryLayout.Compare(left,right,64); Need(diff.ChangedBytes==0,"NONDETERMINISTIC_BYTES");
        OutputIO.SaveJson(b,"determinism.json",new{status="byte-identical",first=a,second=b,sha256=left.Sha256,size=left.Length,diff});
        Console.WriteLine("DETERMINISTIC sha256="+left.Sha256+" size="+left.Length); return 0;
    }
    public static int Main(string[] args)
    {
        try { if(args.SequenceEqual(new[]{"--build"})) return Build(); if(args.Length==3 && args[0]=="--compare") return Compare(args[1],args[2]); throw new GateRefusal("ARGUMENTS --build OR --compare <verified-release> <verified-release>; no -o"); }
        catch(Exception e){Console.Error.WriteLine("BLOCKED "+e);return 2;}
    }
}
