using CCDataFont;
using CCDataNoop;
using UndertaleModLib;
using UndertaleModLib.Models;
using UndertaleModLib.Util;
using static CCDataFont.FontWriter;

static class Tests
{
    static int count; static readonly List<string> passed=new();
    static void Test(string name,Action action) { action();count++;passed.Add(name);Console.WriteLine("OK "+name); }
    static void Reject(Action action) { try{action();}catch(Exception e) when(e is GateRefusal || e is InvalidDataException || e is OverflowException || e is IOException){return;} throw new Exception("Expected refusal"); }
    static void Env(string name,string value,Action action)
    {
        string old=Environment.GetEnvironmentVariable(name);
        try { Environment.SetEnvironmentVariable(name,value); action(); }
        finally { Environment.SetEnvironmentVariable(name,old); }
    }
    static void WorkflowTests(string release)
    {
        string game=Path.Combine(release,"synthetic-game"), backup=Path.Combine(release,"synthetic-backup"), bundle=Path.Combine(release,"synthetic-bundle");
        foreach(string root in new[]{game,backup,bundle}) Directory.CreateDirectory(root);
        string source=Path.Combine(backup,"original.win");
        using(var file=new FileStream(source,FileMode.CreateNew,FileAccess.Write,FileShare.None)) file.WriteByte(1);
        Env("CC_GAME_ROOT",game,()=>Env("CC_DATA_SOURCE",source,()=>Env("CC_FONT_BUNDLE",bundle,()=>{
            Test("backup source independent of absent active data.win",()=>Need(Source==source && !File.Exists(NoopPolicy.Source),"SOURCE_SELECTION"));
            Test("backup does not change game or output root",()=>Need(NoopPolicy.GameRoot==game && NoopPolicy.TempRoot!=backup && ProtectedRoots.SequenceEqual(new[]{game,backup,bundle}),"ROOTS"));
            Test("source fallback compatibility",()=>Env("CC_DATA_SOURCE",null,()=>Need(Source==Path.Combine(game,"data.win"),"SOURCE_FALLBACK")));
            Test("game root still mandatory with backup",()=>Env("CC_GAME_ROOT",null,()=>Reject(()=>{_ = Source;})));
            Test("wrong backup hash never falls back to game",()=>{
                try {ReadSource();} catch(GateRefusal e) when(e.Message.StartsWith("SOURCE_SHA256")) {return;}
                throw new Exception("Expected pinned source refusal");
            });
            foreach(string invalid in new[]{"relative.win",@"C:\backup\..\data.win",@"C:\backup\data.win:stream",@"\\server\share\data.win",@"C:\backup\NUL",@"C:\backup\data.win "})
                Test("unsafe source path "+invalid,()=>Env("CC_DATA_SOURCE",invalid,()=>Reject(()=>{_ = Source;})));
            foreach(string root in new[]{game,backup,bundle})
            {
                Test("reservation protects "+Path.GetFileName(root),()=>Env("CC_OUTPUT_ROOT",root,()=>{
                    var before=Directory.GetFileSystemEntries(root);
                    Reject(()=>OutputIO.Reserve("cc-data-font-"));
                    Need(before.SequenceEqual(Directory.GetFileSystemEntries(root)),"REFUSAL_CREATED_RUN");
                }));
                Test("every write protects "+Path.GetFileName(root),()=>{
                    string dir=Path.Combine(root,"release"); Directory.CreateDirectory(dir);
                    string path=Path.Combine(dir,"probe.bin"); Reject(()=>OutputIO.WriteNew(path,s=>s.WriteByte(1)));
                    Need(!File.Exists(path),"REFUSAL_WROTE_FILE");
                });
            }
            Test("source byte unchanged after refusals",()=>Need(FrozenBytes.ReadFile(source).Span.SequenceEqual(new byte[]{1}),"SOURCE_MUTATED"));
        })));
        object Identity(string path,string hash=NoopPolicy.SourceSha256,int size=182801522)=>new{source=path,sourceSha256=hash,sourceBytes=size};
        Test("historical bundle path is provenance not source identity",()=>ValidateSourceIdentity(System.Text.Json.JsonSerializer.SerializeToElement(Identity(@"C:\absent-historical-source\data.win"))));
        Test("bundle source wrong SHA refused",()=>Reject(()=>ValidateSourceIdentity(System.Text.Json.JsonSerializer.SerializeToElement(Identity(Source,new string('0',64))))));
        Test("bundle source wrong size refused",()=>Reject(()=>ValidateSourceIdentity(System.Text.Json.JsonSerializer.SerializeToElement(Identity(Source,size:1)))));
        Test("bundle source unsafe provenance path refused",()=>Reject(()=>ValidateSourceIdentity(System.Text.Json.JsonSerializer.SerializeToElement(Identity("relative.win")))));
        Test("provenance keeps actual and historical source separately",()=>{
            var b=LoadBundle(); var before=b.Files["report.json"].ToArray();
            Env("CC_DATA_SOURCE",source,()=>{
                var p=System.Text.Json.JsonSerializer.SerializeToElement(Host.InputProvenance(b));
                Need(S(p,"source")==source && S(p,"bundleSource")==S(b.Report,"source") && S(p,"gameRoot")==NoopPolicy.GameRoot,"PROVENANCE_PATHS");
            });
            Need(before.SequenceEqual(b.Files["report.json"]),"REPORT_REWRITTEN");
        });
        Test("comparison binds bytes and current bundle pins",()=>{
            var bytes=FrozenBytes.CopyOf(new byte[]{1,2,3});
            var report=System.Text.Json.Nodes.JsonNode.Parse(Json(new{
                schema="cc-data-font/v1",status="font-candidate-verified",sourceSha256=NoopPolicy.SourceSha256,sourceSha256After=NoopPolicy.SourceSha256,
                outputSha256=bytes.Sha256,outputSize=bytes.Length,bundle=new{files=Pins.Select(x=>new{file=x.Key,bytes=x.Value.Size,sha256=x.Value.Hash}).ToArray()}
            }));
            System.Text.Json.JsonElement Parse()=>FontBuilderCore.Parse(System.Text.Encoding.UTF8.GetBytes(report.ToJsonString()));
            Host.ValidateComparisonReport(Parse(),bytes);
            Reject(()=>Host.ValidateComparisonReport(Parse(),FrozenBytes.CopyOf(new byte[]{1,2,4})));
            report["bundle"]["files"][0]["sha256"]="b84dff5861f1aa5804c379679aa99b15970e1dcb4e2da75722ff6f3a5d528daf";
            Reject(()=>Host.ValidateComparisonReport(Parse(),bytes));
        });
        Test("verify inputs accepts no output or bypass flags",()=>Need(Host.Main(new[]{"--verify-inputs","--skip-hash"})==2,"VERIFY_ARGS"));
        var pinned=LoadBundle();
        foreach(string mutation in new[]{"none","report.json","Nerko.rgba","extra","missing"})
        {
            Test("real bundle relocation/tamper gate "+mutation,()=>{
                string copy=Path.Combine(release,"bundle-"+mutation); Directory.CreateDirectory(copy);
                foreach(var entry in pinned.Files)
                {
                    if(mutation=="missing" && entry.Key=="manifest.json") continue;
                    byte[] bytes=entry.Value.ToArray(); if(entry.Key==mutation) bytes[^1]^=1;
                    using var file=new FileStream(Path.Combine(copy,entry.Key),FileMode.CreateNew,FileAccess.Write,FileShare.None); file.Write(bytes);
                }
                if(mutation=="extra") {using var file=new FileStream(Path.Combine(copy,"unexpected.bin"),FileMode.CreateNew,FileAccess.Write,FileShare.None);file.WriteByte(1);}
                Env("CC_FONT_BUNDLE",copy,()=>{if(mutation=="none") LoadBundle(); else Reject(()=>LoadBundle());});
            });
        }
        Test("verify inputs wrong source creates no output",()=>Env("CC_GAME_ROOT",game,()=>Env("CC_DATA_SOURCE",source,()=>{
            var before=Directory.GetFileSystemEntries(NoopPolicy.TempRoot);
            Need(Host.Main(new[]{"--verify-inputs"})==2,"VERIFY_BAD_SOURCE");
            Need(before.SequenceEqual(Directory.GetFileSystemEntries(NoopPolicy.TempRoot)),"VERIFY_RESERVED_OUTPUT");
        })));
    }
    static IEnumerable<(ushort Code,int W,int H)> Sizes(int w=12,int h=20)=>Enumerable.Range(0,16).Select(i=>((ushort)(0x100+i),w,h));
    static GraphSnapshot Snap(object x)=>GraphSnapshot.Capture(new[]{new GraphRoot("test",x)},new[]{typeof(UndertaleData).Assembly,System.Reflection.Assembly.Load("Underanalyzer")});
    static int Int(byte[] b,int p)=>System.Buffers.Binary.BinaryPrimitives.ReadInt32LittleEndian(b.AsSpan(p,4));
    static void Put(byte[] b,int p,int n)=>System.Buffers.Binary.BinaryPrimitives.WriteInt32LittleEndian(b.AsSpan(p,4),n);
    // Independent raw fixtures: Polish UTF-8, empty record, an alias and two
    // distinct records with equal content. No game read or serializer invocation.
    static (byte[] Bytes,ChunkLayout Chunk) Strg(int offset=112,int extraGap=0,int extraPadding=0,string[] words=null,int[] order=null)
    {
        words ??= new[]{"ą","Ż","","ą"}; order ??= new[]{0,1,0,2,3};
        using var stream=new MemoryStream(); using var w=new BinaryWriter(stream,System.Text.Encoding.UTF8,true);
        w.Write("FORM"u8);w.Write(0);w.Write("JUNK"u8);w.Write(offset-16);w.Write(new byte[offset-16]);
        w.Write("STRG"u8);w.Write(0);w.Write(order.Length);foreach(int id in order) w.Write(0);
        w.Write(new byte[extraGap]); var pointers=new List<int>();
        foreach(string word in words)
        {
            while(stream.Position%4!=0) w.Write((byte)0);
            pointers.Add((int)stream.Position);var bytes=System.Text.Encoding.UTF8.GetBytes(word);
            w.Write(bytes.Length);w.Write(bytes);w.Write((byte)0);
        }
        while(stream.Position%128!=0) w.Write((byte)0);
        w.Write(new byte[extraPadding]); var b=stream.ToArray();Put(b,4,b.Length-8);Put(b,offset+4,b.Length-offset-8);
        for(int i=0;i<order.Length;i++) Put(b,offset+12+4*i,pointers[order[i]]);
        return(b,BinaryLayout.Parse(FrozenBytes.CopyOf(b)).Chunks.Single(c=>c.Name=="STRG"));
    }
    static StrgRelocation.Evidence CheckStrg((byte[] Bytes,ChunkLayout Chunk) a,(byte[] Bytes,ChunkLayout Chunk) b)
        =>StrgRelocation.Validate(FrozenBytes.CopyOf(a.Bytes),FrozenBytes.CopyOf(b.Bytes),a.Chunk,b.Chunk);
    static void StrgTests()
    {
        Test("STRG identity Polish UTF8 empty and aliases",()=>{var a=Strg();var e=CheckStrg(a,a);Need(e.Count==5 && e.UniqueRecords==4,"STRG_FIXTURE");});
        Test("STRG relocation mathematically +16 padding",()=>{var e=CheckStrg(Strg(),Strg(224));Need(e.AfterLength-e.BeforeLength==16 && e.AfterPadding-e.BeforePadding==16,"STRG_PLUS16");});
        Test("STRG relocation decreased padding",()=>{var e=CheckStrg(Strg(224),Strg());Need(e.AfterLength-e.BeforeLength==-16,"STRG_MINUS16");});
        Test("STRG relocation equal length",()=>{var e=CheckStrg(Strg(),Strg(240));Need(e.BeforeLength==e.AfterLength,"STRG_SAME_LENGTH");});
        Test("STRG every aligned offset residue and zero padding",()=>{
            bool zero=false;for(int offset=16;offset<144;offset+=4) {var a=Strg(offset,words:new[]{"abc"},order:new[]{0});var e=CheckStrg(a,Strg(offset+128,words:new[]{"abc"},order:new[]{0}));zero|=e.BeforePadding==0;}Need(zero,"STRG_ZERO_PADDING_CASE");
        });
        Test("STRG empty table relocation",()=>CheckStrg(Strg(words:Array.Empty<string>(),order:Array.Empty<int>()),Strg(224,words:Array.Empty<string>(),order:Array.Empty<int>())));
        Test("STRG changed count",()=>Reject(()=>CheckStrg(Strg(),Strg(order:new[]{0,1,0,2,3,0}))));
        Test("STRG reordered records",()=>Reject(()=>CheckStrg(Strg(),Strg(order:new[]{1,0,0,2,3}))));
        Test("STRG changed alias same content",()=>Reject(()=>CheckStrg(Strg(),Strg(order:new[]{0,1,3,2,3}))));
        Test("STRG changed content equal length",()=>Reject(()=>CheckStrg(Strg(),Strg(words:new[]{"ę","Ż","","ą"}))));
        Test("STRG changed record length and layout",()=>Reject(()=>CheckStrg(Strg(),Strg(words:new[]{"abc","Ż","","ą"}))));
        Test("STRG added zero gap",()=>Reject(()=>CheckStrg(Strg(),Strg(extraGap:4))));
        Test("STRG nonzero record gap",()=>{var a=Strg();a.Bytes[Int(a.Bytes,a.Chunk.Offset+12)+7]=1;Reject(()=>CheckStrg(Strg(),a));});
        Test("STRG nonzero padding",()=>{var a=Strg();a.Bytes[^1]=1;Reject(()=>CheckStrg(Strg(),a));});
        Test("STRG source padding also validated",()=>{var a=Strg();a.Bytes[^1]=1;Reject(()=>CheckStrg(a,Strg()));});
        Test("STRG matching corrupt gaps rejected",()=>{var a=Strg();a.Bytes[Int(a.Bytes,a.Chunk.Offset+12)+7]=1;Reject(()=>CheckStrg(a,a));});
        Test("STRG nonzero terminator",()=>{var a=Strg();a.Bytes[Int(a.Bytes,a.Chunk.Offset+12)+6]=1;Reject(()=>CheckStrg(Strg(),a));});
        Test("STRG empty record terminator not padding",()=>{var a=Strg();a.Bytes[Int(a.Bytes,a.Chunk.Offset+24)+4]=1;Reject(()=>CheckStrg(Strg(),a));});
        Test("STRG arbitrary +16 rejected",()=>Reject(()=>CheckStrg(Strg(),Strg(extraPadding:16))));
        Test("STRG extra aligned 128 zeros rejected",()=>Reject(()=>CheckStrg(Strg(),Strg(extraPadding:128))));
        Test("STRG extra +16 on relocated chunk rejected",()=>Reject(()=>CheckStrg(Strg(),Strg(224,extraPadding:16))));
        Test("STRG missing padding rejected",()=>{var a=Strg();var b=a.Bytes[..^1];Put(b,4,b.Length-8);Put(b,a.Chunk.Offset+4,a.Chunk.Length-1);Reject(()=>CheckStrg(Strg(),(b,a.Chunk with{Length=a.Chunk.Length-1})));});
        Test("STRG stale absolute pointer",()=>{var a=Strg(224);Put(a.Bytes,a.Chunk.Offset+12,Int(a.Bytes,a.Chunk.Offset+12)-112);Reject(()=>CheckStrg(Strg(),a));});
        Test("STRG pointer into table",()=>{var a=Strg();Put(a.Bytes,a.Chunk.Offset+12,a.Chunk.Offset+12);Reject(()=>CheckStrg(Strg(),a));});
        Test("STRG null pointer rejected",()=>{var a=Strg();Put(a.Bytes,a.Chunk.Offset+12,0);Reject(()=>CheckStrg(Strg(),a));});
        Test("STRG overlapping records",()=>{var a=Strg();Put(a.Bytes,a.Chunk.Offset+16,Int(a.Bytes,a.Chunk.Offset+12)+4);Reject(()=>CheckStrg(Strg(),a));});
        Test("STRG pointer past chunk",()=>{var a=Strg();Put(a.Bytes,a.Chunk.Offset+12,int.MaxValue);Reject(()=>CheckStrg(Strg(),a));});
        Test("STRG count overflow",()=>{var a=Strg();Put(a.Bytes,a.Chunk.Offset+8,int.MaxValue);Reject(()=>CheckStrg(Strg(),a));});
        Test("STRG negative count",()=>{var a=Strg();Put(a.Bytes,a.Chunk.Offset+8,-1);Reject(()=>CheckStrg(Strg(),a));});
        Test("STRG record length overflow",()=>{var a=Strg();Put(a.Bytes,Int(a.Bytes,a.Chunk.Offset+12),int.MaxValue);Reject(()=>CheckStrg(Strg(),a));});
        Test("STRG negative record length",()=>{var a=Strg();Put(a.Bytes,Int(a.Bytes,a.Chunk.Offset+12),-1);Reject(()=>CheckStrg(Strg(),a));});
        Test("STRG raw header mismatch",()=>{var a=Strg();Put(a.Bytes,a.Chunk.Offset+4,a.Chunk.Length+16);Reject(()=>CheckStrg(Strg(),a));});
        Test("STRG chunk bounds overflow",()=>{var a=Strg();Reject(()=>CheckStrg(a,(a.Bytes,a.Chunk with{Length=int.MaxValue})));});
        Test("STRG projection changes only Length full snapshot",()=>{
            var a=Strg();var b=Strg(224);var model=new UndertaleChunkSTRG();model.List.Add(new UndertaleString("ą"));
            Set(model,"<Length>k__BackingField",(uint)a.Chunk.Length);var expected=Snap(model);
            Set(model,"<Length>k__BackingField",(uint)b.Chunk.Length);
            StrgRelocation.NormalizeLength(model,FrozenBytes.CopyOf(a.Bytes),FrozenBytes.CopyOf(b.Bytes),a.Chunk,b.Chunk);
            Need(expected.Diff(Snap(model),64).Total==0,"STRG_PROJECTION");
            model.List[0].Content="ę";Need(expected.Diff(Snap(model),64).Total>0,"STRG_NOT_IGNORED");
        });
        Test("STRG refusal never projects Length",()=>{
            var a=Strg();var b=Strg(224);b.Bytes[^1]=1;var model=new UndertaleChunkSTRG();Set(model,"<Length>k__BackingField",(uint)b.Chunk.Length);var before=Snap(model);
            Reject(()=>StrgRelocation.NormalizeLength(model,FrozenBytes.CopyOf(a.Bytes),FrozenBytes.CopyOf(b.Bytes),a.Chunk,b.Chunk));Need(before.Diff(Snap(model),64).Total==0,"STRG_FAILURE_MUTATED");
        });
        Test("STRG reader Length mismatch",()=>{var a=Strg();Reject(()=>StrgRelocation.NormalizeLength(new UndertaleChunkSTRG(),FrozenBytes.CopyOf(a.Bytes),FrozenBytes.CopyOf(a.Bytes),a.Chunk,a.Chunk));});
        Test("STRG integrated gate runs even when Length equal",()=>{
            var a=Strg();var b=Strg(words:new[]{"ę","Ż","","ą"});using var d=new UndertaleData{FORM=new UndertaleChunkFORM()};
            foreach(var c in BinaryLayout.Parse(FrozenBytes.CopyOf(b.Bytes)).Chunks) {UndertaleChunk model=c.Name=="STRG"?new UndertaleChunkSTRG():new UndertaleChunkFONT();Set(model,"<Length>k__BackingField",(uint)c.Length);d.FORM.Chunks.Add(c.Name,model);}
            try {Relocations.Normalize(d,FrozenBytes.CopyOf(a.Bytes),FrozenBytes.CopyOf(b.Bytes),BinaryLayout.Parse(FrozenBytes.CopyOf(a.Bytes)),BinaryLayout.Parse(FrozenBytes.CopyOf(b.Bytes)),new Plan());}
            catch(GateRefusal e) when(e.Message=="STRG_RECORD_BYTES_CHANGED") {return;}throw new Exception("STRG gate bypassed");
        });
    }
    static (UndertaleData d,Plan p) Fixture()
    {
        var d=new UndertaleData {FORM=new UndertaleChunkFORM()};
        foreach(var c in new UndertaleChunk[]{new UndertaleChunkFONT(),new UndertaleChunkTPAG(),new UndertaleChunkTXTR(),new UndertaleChunkTGIN()}) {d.FORM.Chunks.Add(c.Name,c);d.FORM.ChunksTypeDict.Add(c.GetType(),c);}
        for(int i=0;i<15;i++) d.Fonts.Add(new UndertaleFont {Name=new UndertaleString("font"+i)});
        for(int i=0;i<4;i++) d.TextureGroupInfo.Add(new(){Name=new UndertaleString(i==3?"default":"other"+i)});
        var group=d.TextureGroupInfo[3]; var fonts=new List<PlannedFont>();
        for(int i=0;i<4;i++)
        {
            var tex=new UndertaleEmbeddedTexture {TextureInfo=group,IndexInGroup=i}; var old=Page(64,64,tex,i,i);
            d.EmbeddedTextures.Add(tex);d.TexturePageItems.Add(old);group.TexturePages.Add(new(tex,i));
            var f=d.Fonts[11+i];f.Texture=old;f.Glyphs.Add(new(){Character=65,Shift=9});group.Fonts.Add(new(f,11+i));
            var nt=new UndertaleEmbeddedTexture {TextureInfo=group,IndexInGroup=4+i};var np=Page(64,128,nt,4+i,4+i);
            fonts.Add(new(11+i,i,i,Pack(64,64,Sizes()),new byte[64*64*4],new byte[64*128*4],np,nt,
                Sizes().Select(x=>new UndertaleFont.Glyph{Character=x.Code}).ToArray(),new ushort[]{65},default));
        }
        return(d,new Plan{TpagCount=4,TxtrCount=4,GroupCount=4,Fonts=fonts.ToArray()});
    }
    static int Main()
    {
        string release=OutputIO.Reserve("cc-data-font-tests-");
        try
        {
            WorkflowTests(release);
            StrgTests();
            Test("source wrong hash",()=>Reject(()=>FrozenBytes.CopyOf(new byte[]{1}).RequireHash(NoopPolicy.SourceSha256)));
            Test("bundle changed byte",()=>Reject(()=>Pin(new byte[]{2},1,FrozenBytes.Hash(new byte[]{1}))));
            Test("bundle changed length",()=>Reject(()=>Pin(new byte[]{1},2,FrozenBytes.Hash(new byte[]{1}))));
            Test("pinned atlas bundle readback",()=>LoadBundle());
            Test("layout deterministic sorted 2px",()=>{var a=Pack(512,256,Sizes());var b=Pack(512,256,Sizes().Reverse());Need(Json(a).SequenceEqual(Json(b)),"DETERMINISM");Need(a.Height==512 && a.Glyphs[0].Y==258,"SIZE");});
            Test("layout wrap",()=>{var a=Pack(64,128,Sizes(25,20));Need(a.Glyphs[2].Y==152,"WRAP");});
            Test("layout overlarge width",()=>Reject(()=>Pack(512,256,Sizes(509))));
            Test("layout too tall",()=>Reject(()=>Pack(512,4096,Sizes())));
            Test("layout invalid old bounds",()=>Reject(()=>Pack(int.MaxValue,256,Sizes())));
            Test("layout duplicate code",()=>Reject(()=>Pack(512,256,Enumerable.Repeat(((ushort)256,12,20),16))));
            Test("layout overlap",()=>{var a=Pack(512,256,Sizes());var g=a.Glyphs.ToArray();g[1]=g[1] with{X=g[0].X};Reject(()=>ValidateLayout(a with{Glyphs=g},256));});
            Test("layout one-pixel gutter refused",()=>{var a=Pack(512,256,Sizes());var g=a.Glyphs.ToArray();g[1]=g[1] with{X=g[0].X+g[0].W+1};Reject(()=>ValidateLayout(a with{Glyphs=g},256));});
            Test("signed overflow",()=>Reject(()=>Signed(32768)));
            Test("signed underflow",()=>Reject(()=>Signed(-32769)));
            Test("crop overflow",()=>Reject(()=>Crop(new byte[16],2,2,int.MaxValue,0,1,1)));
            Test("blit bounds",()=>Reject(()=>Blit(new byte[16],2,2,new byte[4],1,1,2,0)));
            Test("RGBA straight alpha roundtrip",()=>{byte[] b={255,51,12,0,2,3,4,1};Need(Swap(Swap(b)).SequenceEqual(b),"CHANNELS");});
            Test("identity layout refused",()=>Reject(()=>FontAtlasUmt.RequireIdentityLayout(new(){SourceWidth=4,SourceHeight=4,TargetWidth=3,TargetHeight=4,BoundingWidth=4,BoundingHeight=4})));
            Test("wrong group",()=>Reject(()=>Group(new UndertaleData())));
            Test("group valid synthetic",()=>{var (d,p)=Fixture();Group(d);});
            Test("group wrong name",()=>{var (d,p)=Fixture();d.TextureGroupInfo[3].Name.Content="wrong";Reject(()=>Group(d));});
            Test("group wrong index",()=>{var (d,p)=Fixture();d.EmbeddedTextures[0].IndexInGroup=9;Reject(()=>Group(d));});
            Test("group wrong TextureInfo",()=>{var (d,p)=Fixture();d.EmbeddedTextures[0].TextureInfo=d.TextureGroupInfo[0];Reject(()=>Group(d));});
            Test("group duplicate",()=>{var (d,p)=Fixture();d.TextureGroupInfo[3].TexturePages.Add(new(d.EmbeddedTextures[0],0));Reject(()=>Group(d));});
            Test("group missing font",()=>{var (d,p)=Fixture();d.TextureGroupInfo[3].Fonts.RemoveAt(0);Reject(()=>Group(d));});
            Test("mutator sorted merge and exact inverse full graph",()=>{var (d,p)=Fixture();var before=Snap(d);Apply(d,p);Group(d);Need(d.Fonts[11].Glyphs.Count==17 && d.Fonts[11].Glyphs.Select(g=>g.Character).SequenceEqual(d.Fonts[11].Glyphs.Select(g=>g.Character).Order()),"MERGE");Undo(d,p);Need(before.Diff(Snap(d),64).Total==0,"INVERSE");});
            Test("mutator existing glyph collision",()=>{var (d,p)=Fixture();p.Fonts[0].Additions[0].Character=65;Reject(()=>Apply(d,p));});
            Test("inverse cannot hide unrelated font metrics",()=>{var (d,p)=Fixture();var before=Snap(d);Apply(d,p);d.Fonts[11].RangeEnd++;Undo(d,p);Need(before.Diff(Snap(d),64).Total>0,"RANGE_DIFF");});
            Test("inverse cannot hide old texture change",()=>{var (d,p)=Fixture();var before=Snap(d);Apply(d,p);d.EmbeddedTextures[0].Scaled++;Undo(d,p);Need(before.Diff(Snap(d),64).Total>0,"TXTR_DIFF");});
            Test("inverse cannot hide unmodified font",()=>{var (d,p)=Fixture();var before=Snap(d);Apply(d,p);d.Fonts[0].Bold=true;Undo(d,p);Need(before.Diff(Snap(d),64).Total>0,"OTHER_FONT_DIFF");});
            Test("full snapshot catches old metric",()=>{var g=new UndertaleFont.Glyph {Character=65,Shift=8};var s=Snap(g);g.Shift++;Need(s.Diff(Snap(g),64).Total>0,"METRIC_DIFF");});
            Test("full snapshot catches kerning",()=>{var g=new UndertaleFont.Glyph();var s=Snap(g);g.Kerning.Add(new(){Character=65,ShiftModifier=-1});Need(s.Diff(Snap(g),64).Total>0,"KERN_DIFF");});
            Test("full snapshot catches unknown glyph field",()=>{var g=new UndertaleFont.Glyph();var s=Snap(g);g.UnknownAlwaysZero=1;Need(s.Diff(Snap(g),64).Total>0,"UNKNOWN_DIFF");});
            Test("new Bz2Qoi bounded roundtrip and determinism",()=>{
                var pixels=new byte[32*32*4];new Random(719).NextBytes(pixels);
                GMImage Encode(){var raw=new GMImage(32,32);Swap(pixels).AsSpan().CopyTo(raw.GetRawImageData());return raw.ConvertToBz2Qoi();}
                var a=Encode();var b=Encode();Need(Relocations.Envelope(a).SequenceEqual(Relocations.Envelope(b)),"CODEC_DETERMINISM");
                var t=new UndertaleEmbeddedTexture {TextureWidth=32,TextureHeight=32,TextureLoaded=true};t.TextureData.Image=a;
                var p=Page(32,32,t,0,0);Need(Decode(t,p).SequenceEqual(pixels),"CODEC_PIXELS");
            });
            Test("bounded decode mismatched dimension",()=>{var t=new UndertaleEmbeddedTexture {TextureWidth=31,TextureHeight=32,TextureLoaded=true};t.TextureData.Image=new GMImage(32,32).ConvertToBz2Qoi();Reject(()=>Decode(t,Page(32,32,t,0,0)));});
            Test("actual DLL bookkeeping field contract",()=>{var t=new UndertaleEmbeddedTexture();Need((uint)Get(t,"<_textureBlockSize>k__BackingField")==0 && (long)Get(t.TextureData,"<_maxEndOfStreamPosition>k__BackingField")==-1,"DLL_FIELDS");var c=new UndertaleChunkTXTR();Set(c,"<Length>k__BackingField",47u);Need(c.Length==47,"DLL_LENGTH");});
            Test("persisted full snapshot and report JSON",()=>{var s=Snap(new UndertaleFont.Glyph {Character='ą',Shift=-2});var evidence=OutputIO.Snapshot(release,"fixture.snapshot.jsonl.gz",s);OutputIO.SaveJson(release,"fixture-prewrite.json",new{snapshot=evidence,status="prewrite"});});
            Test("external texture refused",()=>{var t=new UndertaleEmbeddedTexture{TextureExternal=true};Reject(()=>Decode(t,Page(1,1,t,0,0)));});
            Test("existing output cannot overwrite",()=>{string p=Path.Combine(release,"existing.bin");OutputIO.WriteNew(p,s=>s.Write(new byte[]{1,2,3}));Reject(()=>OutputIO.WriteNew(p,s=>s.WriteByte(7)));Need(FrozenBytes.ReadFile(p).Span.SequenceEqual(new byte[]{1,2,3}),"CLOBBER");});
            Test("interrupted write retained and invalid",()=>{string p=Path.Combine(release,"interrupted.bin");Reject(()=>OutputIO.WriteNew(p,s=>{s.Write("FORM"u8);throw new IOException("injected interruption");}));Need(File.Exists(p),"PARTIAL_REMOVED");Reject(()=>BinaryLayout.Parse(FrozenBytes.ReadFile(p)));Reject(()=>OutputIO.WriteNew(p,s=>s.WriteByte(7)));});
            Test("invalid completed write no success",()=>{string p=Path.Combine(release,"invalid.bin");OutputIO.WriteNew(p,s=>s.Write(new byte[]{1,2,3,4,5}));Reject(()=>BinaryLayout.Parse(FrozenBytes.ReadFile(p)));});
            Test("fatal unimportant warning",()=>Reject(()=>NoopPolicy.FatalWarning("synthetic",false)));
            Test("bad writer args no output",()=>Need(Host.Main(new[]{"-o","forbidden"})==2,"ARGS"));
            Test("Unicode UTF8 manifest roundtrip",()=>{var b=LoadBundle();Need(FontBuilderCore.Text(b.Manifest,"required")=="ąćęłńóśźżĄĆĘŁŃÓŚŹŻ","UNICODE");});
            // Real bundle, synthetic old crops: complete placement/copy coverage without game parsing.
            var bundle=LoadBundle();
            Test("bundle B stroke metrics allow lowercase overhang; uppercase unchanged",()=>{
                int[] lower={9,23,20,7},upper={19,52,46,16};
                int[] lowerOffset={-4,-7,-6,-4},upperOffset={-6,-9,-10,-6};
                for(int i=0;i<4;i++) foreach(char c in "łŁ")
                {
                    var m=bundle.Manifest.GetProperty("fonts")[i]; var atlas=m.GetProperty("atlas");
                    var g=m.GetProperty("glyphs").EnumerateArray().Single(g=>S(g,"character")==c.ToString());
                    var pixels=Crop(bundle.Files[S(atlas,"file")],N(atlas,"width"),N(atlas,"height"),N(g,"x"),N(g,"y"),N(g,"width"),N(g,"height"));
                    int right=Enumerable.Range(0,N(g,"width")*N(g,"height")).Where(p=>pixels[4*p+3]>0).Max(p=>p%N(g,"width"))+1+N(g,"offset");
                    var basis=bundle.Report.GetProperty("provenance")[i].GetProperty("donorsAndBases").EnumerateArray().Single(d=>S(d,"character")== (c=='ł'?"l":"L")).GetProperty("model");
                    if(c=='ł')
                    {
                        double em=bundle.Report.GetProperty("provenance")[i].GetProperty("metrics").GetProperty("EmSize").GetDouble();
                        int unit=Math.Max(1,(int)Math.Floor(em/32+0.5));
                        Need(N(g,"shift")==N(basis,"Shift")+unit && N(g,"shift")<right,"LOWERCASE_OVERHANG");
                    }
                    else Need(N(g,"shift")==Math.Max(N(basis,"Shift"),right+1),"UPPERCASE_UNCHANGED");
                    Need(N(g,"shift")== (c=='ł'?lower[i]:upper[i]) && N(g,"offset")== (c=='ł'?lowerOffset[i]:upperOffset[i]),"STROKE_METRICS");
                }
            });
            Test("bundle provenance rejects changed metric",()=>{
                var node=System.Text.Json.Nodes.JsonNode.Parse(bundle.Manifest.GetRawText());
                node["fonts"][0]["glyphs"][0]["shift"]=1;
                Reject(()=>ValidateBundleContent(bundle with{Manifest=FontBuilderCore.Parse(System.Text.Encoding.UTF8.GetBytes(node.ToJsonString()))}));
            });
            for(int i=0;i<4;i++)
            {
                int f=i; Test("bundle layout "+Names[f],()=>{var m=bundle.Manifest.GetProperty("fonts")[f];var l=Pack(OldSize[f].W,OldSize[f].H,m.GetProperty("glyphs").EnumerateArray().Select(g=>((ushort)S(g,"character")[0],N(g,"width"),N(g,"height"))));Console.WriteLine(System.Text.Json.JsonSerializer.Serialize(new{font=Names[f],layout=l}));});
            }
            OutputIO.SaveJson(release,"tests.json",new{status="passed",count,passed,gameRead=false,gameWrite=false}); Console.WriteLine($"PASSED {count}; {release}");return 0;
        }
        catch(Exception e){OutputIO.SaveJson(release,"tests.json",new{status="failed",count,passed,failure=e.ToString()});Console.Error.WriteLine(e);return 1;}
    }
}
