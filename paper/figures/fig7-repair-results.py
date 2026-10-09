import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
plt.rcParams.update({"font.family":"serif","font.serif":["Times New Roman","Times","DejaVu Serif"],
    "font.size":7.5,"axes.linewidth":0.6,"xtick.major.width":0.6,"ytick.major.width":0.6,
    "axes.edgecolor":"#52514e","xtick.color":"#52514e","ytick.color":"#52514e","axes.labelcolor":"#0b0b0b"})
BLUE="#2a78d6"; ORANGE="#eb6834"; INK="#0b0b0b"; MUTED="#8a8984"; GRID="#e4e3df"
fig,(a,b)=plt.subplots(2,1,figsize=(3.45,3.55),gridspec_kw={"height_ratios":[1,1],"hspace":0.62})
# (a) pass rates with 95% Wilson CIs
cats=["HumanEval\n(n=164)","MBPP\n(n=257)","HumanEval-TS\n(n=159)","Pooled\n(n=580)"]
p1=[97.0,96.1,97.5,96.7]; p1ci=[(93.1,98.7),(93.0,97.9),(93.7,99.0),(94.9,97.9)]
ar=[99.4,98.8,98.7,99.0]; arci=[(96.6,99.9),(96.6,99.6),(95.5,99.7),(97.8,99.5)]
x=range(len(cats)); off=0.14
for i in x:
    a.plot([i-off,i-off],p1ci[i],color=ORANGE,lw=1.2,solid_capstyle="round")
    a.plot([i+off,i+off],arci[i],color=BLUE,lw=1.2,solid_capstyle="round")
a.scatter([i-off for i in x],p1,s=22,marker="s",color=ORANGE,edgecolor="white",linewidth=0.8,zorder=3,label="pass@1 (no repair)")
a.scatter([i+off for i in x],ar,s=24,marker="o",color=BLUE,edgecolor="white",linewidth=0.8,zorder=3,label="after bounded repair")
a.set_xticks(list(x)); a.set_xticklabels(cats)
a.set_ylim(92,100.5); a.set_yticks([92,94,96,98,100]); a.set_ylabel("Problems solved (%)")
a.grid(axis="y",color=GRID,lw=0.5); a.set_axisbelow(True)
for s in ["top","right"]: a.spines[s].set_visible(False)
a.legend(loc="lower center",bbox_to_anchor=(0.55,0.99),ncol=2,frameon=False,fontsize=6.8,handletextpad=0.2,columnspacing=1.0)
a.set_title("(a)",fontsize=7.5,loc="left",color=INK,pad=4)
# (b) iteration-cap reconstruction
caps=[0,1,2,3,4,5]; rep=[0,10,13,13,13,13]; att=[0,19,28,31,33,33]
b.axhline(19,color=MUTED,lw=0.7,ls=(0,(2,2)))
b.text(0.05,19.4,"19 first-attempt failures (ceiling)",fontsize=6.5,color="#52514e",va="bottom")
b.plot(caps,rep,color=BLUE,lw=1.4,marker="o",ms=4.5,mec="white",mew=0.8,zorder=3)
for c,r,t in zip(caps,rep,att):
    if c==0: continue
    xy=(7,-9) if c==1 else ((-4,6) if c==5 else (0,6))
    b.annotate(f"{t} attempts" if c==1 else f"{t}",(c,r),textcoords="offset points",xytext=xy,ha="left" if c==1 else ("right" if c==5 else "center"),fontsize=6.3,color="#52514e")
b.axvline(5,color=MUTED,lw=0.7,ls=(0,(1,1.5)))
b.text(4.93,4.0,"default\ncap = 5",fontsize=6.3,color="#52514e",ha="right",va="center")
b.set_xlim(-0.2,5.3); b.set_ylim(0,22); b.set_yticks([0,5,10,13,19])
b.set_xlabel("Iteration cap (max. repair attempts per problem)"); b.set_ylabel("Problems repaired")
b.grid(axis="y",color=GRID,lw=0.5); b.set_axisbelow(True)
for s in ["top","right"]: b.spines[s].set_visible(False)
b.set_title("(b)",fontsize=7.5,loc="left",color=INK,pad=4)
fig.savefig("fig7-repair-results.png",dpi=600,bbox_inches="tight",facecolor="white")
