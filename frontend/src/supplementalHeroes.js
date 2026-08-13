// Official heroes currently absent from the professional BP feature export.
// These are frontend-only placeholders until the data pipeline has learned
// features and match evidence for them.
export const supplementalHeroes = [
  { hero_id: 506, hero_name: "云中君", primary_lane: "jungle", search_aliases: "yunzhongjun yzj" },
  { hero_id: 166, hero_name: "亚瑟", primary_lane: "clash", search_aliases: "yase ys" },
  { hero_id: 137, hero_name: "司马懿", primary_lane: "mid", search_aliases: "simayi smy" },
  { hero_id: 124, hero_name: "周瑜", primary_lane: "mid", search_aliases: "zhouyu zy" },
  { hero_id: 167, hero_name: "孙悟空", primary_lane: "jungle", search_aliases: "sunwukong swk" },
  { hero_id: 177, hero_name: "成吉思汗", primary_lane: "farm", search_aliases: "chengjisihan cjsh" },
  { hero_id: 505, hero_name: "瑶", primary_lane: "roam", search_aliases: "yao y" },
  { hero_id: 504, hero_name: "米莱狄", primary_lane: "mid", search_aliases: "milaidi mld" },
  { hero_id: 183, hero_name: "雅典娜", primary_lane: "jungle", search_aliases: "yadianna yadn" },
].map((hero) => ({ ...hero, catalog_filler: true }));
